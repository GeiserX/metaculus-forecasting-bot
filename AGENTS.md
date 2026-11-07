# AGENTS.md

Forecasting bot for the Metaculus FutureEval bot tournaments. Python 3.11-3.13, `uv`.

## Layout

- `main.py`: the bot (`GeiserXBot`, a `forecasting_tools.ForecastBot`), prompts, entry point and modes.
- `llm_backends.py`: `ClaudeCodeLlm`, which runs `claude -p` with a minimal environment, its own config and work directories under `STATE_DIR` (overridable with `CLAUDE_CONFIG_DIR` / `CLAUDE_WORKDIR`), no MCP servers and an explicit tool list, and logs usage per call.
- `parsing.py`: deterministic parsing of the final answer lines. Tests in `tests/`.
- `run.sh`: scheduled entry point (lock, secrets file, `LIVE` guard, logs).
- `docs/`: configuration and how it works.

## Rules

- Tournament rules: never test on open or upcoming tournament questions, never rerun a question because a forecast looks wrong, one forecast per question. Test with `./run.sh test_questions [--dry-run]` or `./run.sh question --url <closed question>`. `--dry-run` is refused in tournament mode.
- Keep a question at 1 research call plus `PREDICTIONS_PER_QUESTION` forecast calls; do not add hidden LLM calls (summaries, parser models).
- Never commit secrets, tokens, machine names or local paths.
- Run `uv run python -m pytest -q tests` before pushing.
