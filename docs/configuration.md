# Configuration

All settings are environment variables. `run.sh` reads them from `$SECRETS_FILE`.

| Variable | Default | Meaning |
|---|---|---|
| `STATE_DIR` | `~/.metaculus-bot` | Logs, reports, usage files, Claude Code config and work directories |
| `SECRETS_FILE` | `$STATE_DIR/secrets.env` | Env file with the tokens below (mode 600) |
| `METACULUS_TOKEN` | required | Bot account token (Settings > My Forecasting Bots) |
| `CLAUDE_CODE_OAUTH_TOKEN` | required for `claude-code` | Long-lived token from `claude setup-token` |
| `LIVE` | `0` | `1` lets `run.sh tournament` post to the live tournaments |
| `LLM_BACKEND` | `claude-code` | `claude-code` or `litellm` |
| `RESEARCH_MODEL` | `claude-sonnet-5-5` | Model for the research call |
| `FORECAST_MODELS` | `claude-opus-5-5` | Comma-separated; forecast calls rotate through them |
| `PREDICTIONS_PER_QUESTION` | `5` | Forecast calls per question; the median is posted |
| `RESEARCH_TIMEOUT` / `FORECAST_TIMEOUT` | `600` / `300` | Seconds per Claude Code call (one retry on failure) |
| `RESEARCH_EFFORT` / `FORECAST_EFFORT` | unset | Claude Code `--effort` level |
| `CLAUDE_BIN` | `claude` | Path to the Claude Code binary |
| `MAX_CONCURRENT_QUESTIONS` | `1` | Questions researched at the same time |

With `LLM_BACKEND=litellm`, `RESEARCH_MODEL` and `FORECAST_MODELS` are litellm model names (for example `openrouter/openai/gpt-5.2:online`) and the provider key (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, ...) goes in the same file.

## Modes

| Command | What it does |
|---|---|
| `./run.sh tournament` | Seasonal FutureEval tournament and MiniBench; skips questions already forecast; needs `LIVE=1` |
| `./run.sh test_questions` | The unscored bot-testing-area; reforecasts every run |
| `./run.sh question --url <url>` | One question, for testing; never point it at a live tournament question |
| `./run.sh metaculus_cup` | The Metaculus Cup (bots are not prize-eligible there) |

Add `--dry-run` to any mode to forecast without posting.
