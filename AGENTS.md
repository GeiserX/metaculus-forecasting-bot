# AGENTS.md

Forecasting bot for the Metaculus FutureEval bot tournaments. Python 3.11-3.13, `uv`.

## Layout

- `main.py`: the bot (`GeiserXBot`, a `forecasting_tools.ForecastBot`), prompts, entry point and modes.
- `llm_backends.py`: `ClaudeCodeLlm`, which runs `claude -p` in a clean, isolated environment and logs usage per call.
- `parsing.py`: deterministic parsing of the final answer lines. Tests in `tests/`.
- `run.sh`: scheduled entry point (lock, secrets file, `LIVE` guard, logs).
- `docs/`: configuration and how it works.

## Rules

- Tournament rules: never test on open or upcoming tournament questions, never rerun a question because a forecast looks wrong, one forecast per question. Test with `./run.sh test_questions` or `--dry-run`.
- Keep a question at 1 research call plus `PREDICTIONS_PER_QUESTION` forecast calls; do not add hidden LLM calls (summaries, parser models).
- Never commit secrets, tokens, machine names or local paths.
- Run `uv run python -m pytest -q tests` before pushing.
