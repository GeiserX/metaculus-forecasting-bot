<h1 align="center">metaculus-forecasting-bot</h1>

<p align="center">
  <a href="https://github.com/GeiserX/metaculus-forecasting-bot/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/GeiserX/metaculus-forecasting-bot/ci.yml?style=flat-square&label=CI" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/GeiserX/metaculus-forecasting-bot?style=flat-square" alt="License"></a>
</p>

metaculus-forecasting-bot is the forecasting bot behind `GeiserX-bot` in the Metaculus FutureEval bot tournaments: the bi-weekly MiniBench and the seasonal tournament. It uses Claude Code in headless mode (`claude -p`) as its model, and Claude Code's own web search does the research. It runs unattended: it fetches open questions, researches each one, forecasts, and posts the forecast and a private comment with its reasoning through the Metaculus API.

It starts from the official template, https://github.com/Metaculus/metac-bot-template, and uses the https://github.com/Metaculus/forecasting-tools package for the Metaculus API.

## Features

- One research call per question: Claude Code with WebSearch and WebFetch, asked for dated values, scheduled events, base rates and prediction-market forecasts
- Five forecast calls per question with no tools; the median is posted
- Calibration rules in every prompt: reference-class base rate, status quo weight, exact resolution criteria, no extremes
- Binary forecasts clamped to 3-97%, every multiple-choice option at least 1% (scoring is log-based, so one confident miss costs more than hedging loses)
- Deterministic answer parsing, no parser model, so a question costs exactly 1 + 5 calls
- Every Claude Code call runs in a clean environment with its own config directory: no user settings, hooks, MCP servers or CLAUDE.md files
- Per-call usage log (tokens, seconds, notional cost) and a per-run summary
- `LLM_BACKEND=litellm` swaps in any API model through litellm instead

## Quick start

Needs Python 3.11-3.13, `uv`, Claude Code, a Metaculus bot token and a Claude Code token from `claude setup-token`.

```bash
git clone https://github.com/GeiserX/metaculus-forecasting-bot && cd metaculus-forecasting-bot
uv sync
mkdir -p ~/.metaculus-bot && cp .env.template ~/.metaculus-bot/secrets.env && chmod 600 ~/.metaculus-bot/secrets.env
# fill METACULUS_TOKEN and CLAUDE_CODE_OAUTH_TOKEN in that file, then:
./run.sh test_questions --dry-run
```

The dry run forecasts the unscored bot-testing-area questions and prints a summary with the usage. Drop `--dry-run` to post there, and set `LIVE=1` in the secrets file to let `./run.sh tournament` forecast the live tournaments. Run it every 20 minutes (questions stay open for 3 hours) with cron or `deploy/launchd.example.plist`.

## Documentation

- [Configuration](docs/configuration.md): every setting and its default
- [How it works](docs/how-it-works.md): the pipeline, the prompts, the logs, and the tournament rules the bot follows

## License

[MIT](LICENSE)
