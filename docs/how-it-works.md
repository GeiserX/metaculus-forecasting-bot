# How it works

## Pipeline

For each open question that the bot has not forecast yet:

1. **Research.** One `claude -p` call with only the WebSearch and WebFetch tools. The prompt asks for the latest dated value of what the question measures, scheduled events before resolution, base rates, and prediction-market or expert forecasts, in at most about 600 words and 8 searches.
2. **Forecast.** Five `claude -p` calls with no tools, each given the question, the resolution criteria and the research. Each prompt asks the model to state the time left, the status quo, scenarios each way and a reference-class base rate, and to follow explicit calibration rules. The prompts come from the official template, with those additions.
3. **Parse.** The final lines (`Probability: ZZ%`, one line per option, or six percentiles) are parsed with regular expressions. A sample that does not parse is dropped.
4. **Aggregate and post.** The median of the samples is posted (for numeric and date questions, the median of the CDFs), with binary forecasts clamped to 3-97% and multiple-choice options floored at 1%. The reasoning goes into a private comment, which Metaculus makes public after the question closes.

## Claude Code isolation

Each call runs with a minimal environment (`HOME`, `PATH`, the token), `CLAUDE_CONFIG_DIR` pointing at an empty directory under `$STATE_DIR`, an empty working directory, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`, an explicit `--tools` list and a replaced system prompt. Nothing from the user's own Claude Code setup reaches the bot.

## Logs

| File | Content |
|---|---|
| `$STATE_DIR/logs/YYYY-MM-DD.log` | Full run output, including research and reasoning |
| `$STATE_DIR/usage.tsv` | One row per Claude Code call: time, question, role, model, ok, seconds, turns, tokens, notional USD |
| `$STATE_DIR/costs.tsv` | One row per run: mode, questions forecast, calls, tokens, notional USD |
| `$STATE_DIR/logs/skipped.log` | Live runs skipped because `LIVE` is not 1 |

Notional USD is what Claude Code reports the call would cost at API prices.

## Tournament rules the bot follows

From the FutureEval resources page: one forecast per question in the bot tournaments, no human influence on any forecast, never rerun a question because the result looks wrong, test changes only on the bot-testing-area or on closed questions, and leave a comment with the reasoning on every forecast.
