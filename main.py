"""
GeiserX-bot: a forecasting bot for the Metaculus FutureEval bot tournaments (MiniBench and the
seasonal tournament). Built on the official template (Metaculus/metac-bot-template) and the
forecasting-tools package.

Per question: one research call (Claude Code headless with WebSearch/WebFetch), then N forecast
calls (default 5, no tools). The answers are parsed deterministically and the median is posted,
together with a private comment holding the reasoning. LLM_BACKEND=litellm swaps in any
litellm model through API keys instead.
"""
import argparse
import asyncio
import itertools
import logging
import os
from datetime import datetime, timezone
from typing import Literal

import dotenv

from bot_helpers import (
    print_run_summary_banner,
    print_startup_banner,
    silence_noisy_dependencies,
)

silence_noisy_dependencies()

from forecasting_tools import (  # noqa: E402
    BinaryQuestion,
    ConditionalPrediction,
    ConditionalQuestion,
    DateQuestion,
    ForecastBot,
    GeneralLlm,
    MetaculusClient,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    Percentile,
    PredictedOption,
    PredictedOptionList,
    PredictionAffirmed,
    PredictionTypes,
    ReasonedPrediction,
    clean_indents,
)

from llm_backends import ClaudeCodeLlm, usage  # noqa: E402
from parsing import (  # noqa: E402
    parse_date_percentiles,
    parse_options,
    parse_percentiles,
    parse_probability,
)

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

# Calibration: keep forecasts away from 0 and 1. Spot peer score is log-based, so one
# confident miss costs far more than a slightly hedged hit earns.
BINARY_MIN, BINARY_MAX = 0.03, 0.97
MC_MIN_OPTION = 0.01

CALIBRATION_NOTES = """
Calibration rules you follow:
- Start from a base rate: name a reference class of similar past situations and how often they resolved Yes, then adjust for the specifics.
- The world usually changes slowly; weight the status quo heavily unless the research shows a concrete, dated reason for change.
- Check the exact resolution criteria and the time left. Questions that need an event to happen in a short window usually resolve No.
- Avoid extreme probabilities. Go below 5% or above 95% only when the outcome is nearly settled by known facts.
- Do not anchor on round numbers or on the question's framing.
""".strip()

RESEARCH_SYSTEM = (
    "You are a research assistant to a professional forecaster. Use web search and web fetch to "
    "find current, dated facts. Be concise and factual, cite sources with dates, and never give a "
    "forecast yourself. Reply in plain text only."
)
FORECAST_SYSTEM = (
    "You are a calibrated professional forecaster. You reason step by step from the information "
    "given and end with the exact answer format requested. Reply in plain text only."
)


def _env_list(name: str, default: str) -> list[str]:
    return [m.strip() for m in os.getenv(name, default).split(",") if m.strip()]


def build_llms() -> tuple[object, list[object], str]:
    """Return (researcher, forecasters, description) for the configured backend."""
    backend = os.getenv("LLM_BACKEND", "claude-code")
    if backend == "claude-code":
        research_model = os.getenv("RESEARCH_MODEL", "claude-sonnet-5-5")
        forecast_models = _env_list("FORECAST_MODELS", "claude-opus-5-5")
        researcher = ClaudeCodeLlm(
            model=research_model, role="research", system_prompt=RESEARCH_SYSTEM,
            tools=("WebSearch", "WebFetch"),
            timeout=int(os.getenv("RESEARCH_TIMEOUT", "600")),
            effort=os.getenv("RESEARCH_EFFORT") or None,
        )
        forecasters = [
            ClaudeCodeLlm(
                model=m, role="forecast", system_prompt=FORECAST_SYSTEM, tools=(),
                timeout=int(os.getenv("FORECAST_TIMEOUT", "300")),
                effort=os.getenv("FORECAST_EFFORT") or None,
            )
            for m in forecast_models
        ]
    elif backend == "litellm":
        timeout = int(os.getenv("LLM_TIMEOUT", "300"))
        research_model = os.environ["RESEARCH_MODEL"]
        forecast_models = _env_list("FORECAST_MODELS", "")
        researcher = GeneralLlm(model=research_model, timeout=timeout, allowed_tries=3)
        forecasters = [GeneralLlm(model=m, timeout=timeout, allowed_tries=3) for m in forecast_models]
    else:
        raise SystemExit(f"Unknown LLM_BACKEND {backend!r} (use claude-code or litellm)")
    if not forecasters:
        raise SystemExit("FORECAST_MODELS is empty")
    return researcher, forecasters, f"{backend}: research={research_model} forecast={forecast_models}"


def check_environment() -> None:
    problems = []
    if not os.getenv("METACULUS_TOKEN"):
        problems.append("METACULUS_TOKEN is not set (Metaculus > Settings > My Forecasting Bots).")
    if os.getenv("LLM_BACKEND", "claude-code") == "claude-code" and not os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        problems.append("CLAUDE_CODE_OAUTH_TOKEN is not set (create one with `claude setup-token`).")
    if problems:
        for p in problems:
            print(f"Setup problem: {p}")
        raise SystemExit(1)


async def _ask(llm: object, prompt: str, tag: str) -> str:
    if isinstance(llm, ClaudeCodeLlm):
        return await llm.invoke(prompt, tag=tag)
    return await llm.invoke(prompt)  # GeneralLlm


def _tag(question: MetaculusQuestion) -> str:
    return (question.page_url or "").rstrip("/").split("/")[-1]


class GeiserXBot(ForecastBot):
    """
    Changes from the template bot:
    - research by Claude Code headless with its own web search, asked for dated values,
      scheduled events, base rates and market forecasts;
    - N forecasts per question rotating through FORECAST_MODELS, median posted;
    - explicit calibration rules in every prompt;
    - deterministic answer parsing (no parser LLM), so a question costs 1 + N calls;
    - binary clamped to [3%, 97%], multiple-choice options floored at 1%.
    """

    _max_concurrent_questions = int(os.getenv("MAX_CONCURRENT_QUESTIONS", "1"))
    _concurrency_limiter = asyncio.Semaphore(_max_concurrent_questions)

    def __init__(self, *args, researcher: object, forecasters: list[object], **kwargs):
        super().__init__(*args, **kwargs)
        self._researcher = researcher
        self._forecaster_cycle = itertools.cycle(forecasters)

    async def _forecast_text(self, question: MetaculusQuestion, prompt: str) -> str:
        llm = next(self._forecaster_cycle)
        text = await _ask(llm, prompt, _tag(question))
        logger.info(f"Reasoning for URL {question.page_url} ({llm.model}): {text}")
        return f"[model: {llm.model}]\n{text}"

    ##################################### RESEARCH #####################################

    async def run_research(self, question: MetaculusQuestion) -> str:
        async with self._concurrency_limiter:
            prompt = clean_indents(
                f"""
                A forecaster will forecast the question below. Research it on the web and write a concise rundown (at most about 600 words) of the most relevant facts, including whether the question would resolve Yes or No on current information.
                Search for and report, each with its date and source:
                - the latest value or status of whatever the question measures, and its recent trend;
                - scheduled events before the resolution date (votes, releases, deadlines, data publications);
                - base rates: how often comparable situations resolved each way in the past;
                - forecasts from prediction markets (Polymarket, Kalshi, Manifold), Metaculus, or experts, if any exist.
                Use at most 8 searches. Say plainly when you could not find something. Do not give a forecast.

                Today is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}.

                Question:
                {question.question_text}

                Resolution criteria:
                {question.resolution_criteria}

                {question.fine_print}
                """
            )
            research = await _ask(self._researcher, prompt, _tag(question))
            logger.info(f"Found Research for URL {question.page_url}:\n{research}")
            return research

    ##################################### BINARY QUESTIONS #####################################

    async def _run_forecast_on_binary(
        self, question: BinaryQuestion, research: str
    ) -> ReasonedPrediction[float]:
        prompt = clean_indents(
            f"""
            You are a professional forecaster.

            Question:
            {question.question_text}

            Question background:
            {question.background_info}

            This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:
            {research}

            Today is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A brief description of a scenario that results in a No outcome.
            (d) A brief description of a scenario that results in a Yes outcome.
            (e) A base rate from a named reference class, and how this case differs from it.

            {CALIBRATION_NOTES}
            {self._get_conditional_disclaimer_if_necessary(question)}

            The last thing you write is your final answer as: "Probability: ZZ%", 0-100
            """
        )
        reasoning = await self._forecast_text(question, prompt)
        value = max(BINARY_MIN, min(BINARY_MAX, parse_probability(reasoning)))
        logger.info(f"Forecasted URL {question.page_url} with prediction: {value}.")
        return ReasonedPrediction(prediction_value=value, reasoning=reasoning)

    ##################################### MULTIPLE CHOICE QUESTIONS #####################################

    async def _run_forecast_on_multiple_choice(
        self, question: MultipleChoiceQuestion, research: str
    ) -> ReasonedPrediction[PredictedOptionList]:
        prompt = clean_indents(
            f"""
            You are a professional forecaster.

            Question:
            {question.question_text}

            The options are: {question.options}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:
            {research}

            Today is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A description of a scenario that results in an unexpected outcome.

            {self._get_conditional_disclaimer_if_necessary(question)}
            You remember that good forecasters put extra weight on the status quo outcome, and leave some moderate probability on most options to account for unexpected outcomes.
            {CALIBRATION_NOTES}

            The last thing you write is your final probabilities for the N options, one per line, in this order {question.options}, using each option name exactly as given:
            Option_A: Probability_A%
            Option_B: Probability_B%
            ...
            Option_N: Probability_N%
            """
        )
        reasoning = await self._forecast_text(question, prompt)
        probs = parse_options(reasoning, question.options)
        floored = {k: max(v, MC_MIN_OPTION) for k, v in probs.items()}
        total = sum(floored.values())
        prediction = PredictedOptionList(
            predicted_options=[
                PredictedOption(option_name=k, probability=v / total) for k, v in floored.items()
            ]
        )
        logger.info(f"Forecasted URL {question.page_url} with prediction: {prediction}.")
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    ##################################### NUMERIC AND DATE QUESTIONS #####################################

    async def _run_forecast_on_numeric(
        self, question: NumericQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = self._create_upper_and_lower_bound_messages(question)
        prompt = clean_indents(
            f"""
            You are a professional forecaster.

            Question:
            {question.question_text}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Units for answer: {question.unit_of_measure if question.unit_of_measure else "Not stated (please infer this)"}

            Your research assistant says:
            {research}

            Today is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}.

            {lower_bound_message}
            {upper_bound_message}

            Formatting Instructions:
            - Give your answer in the units requested (e.g. whether you represent a number as 1,000,000 or 1 million), as plain numbers.
            - Never use scientific notation or words like "million" in the final lines.
            - Always start with a smaller number (more negative if negative) and then increase from there.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The outcome if nothing changed.
            (c) The outcome if the current trend continued.
            (d) The expectations of experts and markets.
            (e) A brief description of an unexpected scenario that results in a low outcome.
            (f) A brief description of an unexpected scenario that results in a high outcome.

            {self._get_conditional_disclaimer_if_necessary(question)}
            You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

            The last thing you write is your final answer as:
            "
            Percentile 10: XX (lowest number value)
            Percentile 20: XX
            Percentile 40: XX
            Percentile 60: XX
            Percentile 80: XX
            Percentile 90: XX (highest number value)
            "
            """
        )
        reasoning = await self._forecast_text(question, prompt)
        percentiles = [Percentile(percentile=p, value=v) for p, v in parse_percentiles(reasoning)]
        prediction = NumericDistribution.from_question(percentiles, question)
        logger.info(f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}.")
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    async def _run_forecast_on_date(
        self, question: DateQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = self._create_upper_and_lower_bound_messages(question)
        prompt = clean_indents(
            f"""
            You are a professional forecaster.

            Question:
            {question.question_text}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:
            {research}

            Today is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}.

            {lower_bound_message}
            {upper_bound_message}

            Formatting Instructions:
            - This is a date question, and the answer must be expressed as dates in the format YYYY-MM-DD. If hours matter, use YYYY-MM-DDTHH:MM:SSZ (UTC).
            - The dates must be in chronological order, the earliest at percentile 10.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The outcome if nothing changed.
            (c) The outcome if the current trend continued.
            (d) The expectations of experts and markets.
            (e) A brief description of an unexpected scenario that results in a low outcome.
            (f) A brief description of an unexpected scenario that results in a high outcome.

            {self._get_conditional_disclaimer_if_necessary(question)}
            You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

            The last thing you write is your final answer as:
            "
            Percentile 10: YYYY-MM-DD (oldest date)
            Percentile 20: YYYY-MM-DD
            Percentile 40: YYYY-MM-DD
            Percentile 60: YYYY-MM-DD
            Percentile 80: YYYY-MM-DD
            Percentile 90: YYYY-MM-DD (newest date)
            "
            """
        )
        reasoning = await self._forecast_text(question, prompt)
        percentiles = [Percentile(percentile=p, value=v) for p, v in parse_date_percentiles(reasoning)]
        prediction = NumericDistribution.from_question(percentiles, question)
        logger.info(f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}.")
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    def _create_upper_and_lower_bound_messages(
        self, question: NumericQuestion | DateQuestion
    ) -> tuple[str, str]:
        if isinstance(question, NumericQuestion):
            upper = question.nominal_upper_bound if question.nominal_upper_bound is not None else question.upper_bound
            lower = question.nominal_lower_bound if question.nominal_lower_bound is not None else question.lower_bound
            unit = question.unit_of_measure
        elif isinstance(question, DateQuestion):
            upper = question.upper_bound.date().isoformat()
            lower = question.lower_bound.date().isoformat()
            unit = ""
        else:
            raise ValueError()
        if question.open_upper_bound:
            upper_msg = f"The question creator thinks the number is likely not higher than {upper} {unit}."
        else:
            upper_msg = f"The outcome can not be higher than {upper} {unit}."
        if question.open_lower_bound:
            lower_msg = f"The question creator thinks the number is likely not lower than {lower} {unit}."
        else:
            lower_msg = f"The outcome can not be lower than {lower} {unit}."
        return upper_msg, lower_msg

    ##################################### CONDITIONAL QUESTIONS #####################################

    async def _run_forecast_on_conditional(
        self, question: ConditionalQuestion, research: str
    ) -> ReasonedPrediction[ConditionalPrediction]:
        parent_info, full_research = await self._get_question_prediction_info(
            question.parent, research, "parent"
        )
        child_info, full_research = await self._get_question_prediction_info(
            question.child, research, "child"
        )
        yes_info, full_research = await self._get_question_prediction_info(
            question.question_yes, full_research, "yes"
        )
        no_info, full_research = await self._get_question_prediction_info(
            question.question_no, full_research, "no"
        )
        full_reasoning = clean_indents(
            f"""
            ## Parent Question Reasoning
            {parent_info.reasoning}
            ## Child Question Reasoning
            {child_info.reasoning}
            ## Yes Question Reasoning
            {yes_info.reasoning}
            ## No Question Reasoning
            {no_info.reasoning}
        """
        )
        full_prediction = ConditionalPrediction(
            parent=parent_info.prediction_value,  # type: ignore
            child=child_info.prediction_value,  # type: ignore
            prediction_yes=yes_info.prediction_value,  # type: ignore
            prediction_no=no_info.prediction_value,  # type: ignore
        )
        return ReasonedPrediction(
            reasoning=full_reasoning, prediction_value=full_prediction
        )

    async def _get_question_prediction_info(
        self, question: MetaculusQuestion, research: str, question_type: str
    ) -> tuple[ReasonedPrediction[PredictionTypes | PredictionAffirmed], str]:
        from forecasting_tools.data_models.data_organizer import DataOrganizer

        previous_forecasts = question.previous_forecasts
        if (
            question_type in ["parent", "child"]
            and previous_forecasts
            and question_type not in self.force_reforecast_in_conditional
        ):
            # TODO: add option to not affirm current parent/child forecasts, create new forecast
            previous_forecast = previous_forecasts[-1]
            current_utc_time = datetime.now(timezone.utc)
            if (
                previous_forecast.timestamp_end is None
                or previous_forecast.timestamp_end > current_utc_time
            ):
                pretty_value = DataOrganizer.get_readable_prediction(previous_forecast)  # type: ignore
                prediction = ReasonedPrediction(
                    prediction_value=PredictionAffirmed(),
                    reasoning=f"Already existing forecast reaffirmed at {pretty_value}.",
                )
                return (prediction, research)  # type: ignore
        info = await self._make_prediction(question, research)
        full_research = self._add_reasoning_to_research(research, info, question_type)
        return info, full_research  # type: ignore

    def _add_reasoning_to_research(
        self,
        research: str,
        reasoning: ReasonedPrediction[PredictionTypes],
        question_type: str,
    ) -> str:
        from forecasting_tools.data_models.data_organizer import DataOrganizer

        question_type = question_type.title()
        return clean_indents(
            f"""
            {research}
            ---
            ## {question_type} Question Information
            You have previously forecasted the {question_type} Question to the value: {DataOrganizer.get_readable_prediction(reasoning.prediction_value)}
            This is relevant information for your current forecast, but it is NOT your current forecast, but previous forecasting information that is relevant to your current forecast.
            The reasoning for the {question_type} Question was as such:
            ```
            {reasoning.reasoning}
            ```
            This is absolutely essential: do NOT use this reasoning to re-forecast the {question_type} question.
            """
        )

    def _get_conditional_disclaimer_if_necessary(
        self, question: MetaculusQuestion
    ) -> str:
        if question.conditional_type not in ["yes", "no"]:
            return ""
        return clean_indents(
            """
            As you are given a conditional question with a parent and child, you are to only forecast the **CHILD** question, given the parent question's resolution.
            You never re-forecast the parent question under any circumstances, but you use probabilistic reasoning, strongly considering the parent question's resolution, to forecast the child question.
            """
        )



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Run GeiserX-bot")
    parser.add_argument(
        "--mode", choices=["tournament", "metaculus_cup", "test_questions", "question"], default="tournament",
        help="tournament = seasonal FutureEval + MiniBench (default)",
    )
    parser.add_argument("--url", help="Question URL for --mode question (testing only, never a live tournament question)")
    parser.add_argument("--dry-run", action="store_true", help="Forecast but do not post to Metaculus")
    args = parser.parse_args()
    run_mode: Literal["tournament", "metaculus_cup", "test_questions", "question"] = args.mode

    if args.dry_run and run_mode == "tournament":
        # A dry run here would preview forecasts on open tournament questions, which the
        # tournament rules forbid. Test with test_questions or a closed question instead.
        raise SystemExit("--dry-run is not allowed in tournament mode; use --mode test_questions")
    check_environment()
    publish = not args.dry_run
    print_startup_banner(run_mode, will_publish=publish)
    researcher, forecasters, description = build_llms()
    print(f"LLMs: {description}")

    bot = GeiserXBot(
        research_reports_per_question=1,
        predictions_per_research_report=int(os.getenv("PREDICTIONS_PER_QUESTION", "5")),
        use_research_summary_to_forecast=False,
        enable_summarize_research=False,
        publish_reports_to_metaculus=publish,
        folder_to_save_reports_to=os.getenv("REPORTS_DIR") or None,
        skip_previously_forecasted_questions=True,
        extra_metadata_in_explanation=True,
        llms={
            "default": description,
            "summarizer": "none",
            "researcher": "see default",
            "parser": "deterministic",
        },
        researcher=researcher,
        forecasters=forecasters,
    )

    TOURNAMENT_URLS = {
        "tournament": "https://www.metaculus.com/tournament/fall-futureeval-2026/",
        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-fall-2026/",
        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",
    }

    client = MetaculusClient()
    if run_mode == "tournament":
        forecast_reports = asyncio.run(
            bot.forecast_on_tournament(client.CURRENT_AI_COMPETITION_ID, return_exceptions=True)
        ) + asyncio.run(bot.forecast_on_tournament(client.CURRENT_MINIBENCH_ID, return_exceptions=True))
    elif run_mode == "metaculus_cup":
        bot.skip_previously_forecasted_questions = False
        forecast_reports = asyncio.run(
            bot.forecast_on_tournament(client.CURRENT_METACULUS_CUP_ID, return_exceptions=True)
        )
    elif run_mode == "question":
        bot.skip_previously_forecasted_questions = False
        forecast_reports = asyncio.run(
            bot.forecast_questions([client.get_question_by_url(args.url)], return_exceptions=True)
        )
    else:  # test_questions: bot-testing-area, unscored, safe to rerun
        bot.skip_previously_forecasted_questions = False
        forecast_reports = asyncio.run(bot.forecast_on_tournament("bot-testing-area", return_exceptions=True))

    bot.log_report_summary(forecast_reports)
    print_run_summary_banner(forecast_reports, will_publish=publish, tournament_url=TOURNAMENT_URLS.get(run_mode))

    posted = sum(1 for r in forecast_reports if not isinstance(r, BaseException))
    print(
        f"LLM usage this run: {usage.calls} calls ({usage.failed} failed) {usage.by_role}, "
        f"{usage.input_tokens} input / {usage.output_tokens} output tokens, "
        f"notional ${usage.notional_usd:.2f}"
    )
    cost_log = os.getenv("COST_LOG")
    if cost_log:
        new = not os.path.exists(cost_log)
        with open(cost_log, "a") as f:
            if new:
                f.write("time\tmode\tpublished\tquestions\tcalls\tfailed_calls\tinput_tokens\toutput_tokens\tnotional_usd\n")
            f.write(
                f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\t{run_mode}\t{int(publish)}\t{posted}\t"
                f"{usage.calls}\t{usage.failed}\t{usage.input_tokens}\t{usage.output_tokens}\t{usage.notional_usd:.4f}\n"
            )
