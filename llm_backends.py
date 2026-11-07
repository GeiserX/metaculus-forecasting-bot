"""
LLM backends for the bot.

ClaudeCodeLlm runs Claude Code headless (`claude -p`) as the model. Research calls get
Claude Code's own WebSearch and WebFetch tools; forecast calls get no tools. Each call runs
in a clean environment with its own config directory and an empty working directory, so no
user settings, hooks, MCP servers or CLAUDE.md files leak in. Auth is CLAUDE_CODE_OAUTH_TOKEN
(from `claude setup-token`).

Every call appends one row to USAGE_LOG (TSV) and to the in-process `usage` totals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

USAGE_COLUMNS = [
    "time", "tag", "role", "model", "ok", "seconds", "turns",
    "input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "notional_usd",
]


@dataclass
class UsageTotals:
    calls: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    notional_usd: float = 0.0
    by_role: dict = field(default_factory=dict)

    def add(self, role: str, ok: bool, inp: int, out: int, usd: float) -> None:
        self.calls += 1
        self.failed += 0 if ok else 1
        self.input_tokens += inp
        self.output_tokens += out
        self.notional_usd += usd
        self.by_role[role] = self.by_role.get(role, 0) + 1


usage = UsageTotals()


def _log_usage(row: dict) -> None:
    path = os.getenv("USAGE_LOG")
    if not path:
        return
    new = not os.path.exists(path)
    with open(path, "a") as f:
        if new:
            f.write("\t".join(USAGE_COLUMNS) + "\n")
        f.write("\t".join(str(row.get(c, "")) for c in USAGE_COLUMNS) + "\n")


class ClaudeCodeLlm:
    def __init__(
        self,
        model: str,
        role: str,
        system_prompt: str,
        tools: tuple[str, ...] = (),
        timeout: int = 300,
        effort: str | None = None,
        allowed_tries: int = 2,
    ) -> None:
        self.model = model
        self.role = role
        self.system_prompt = system_prompt
        self.tools = tools
        self.timeout = timeout
        self.effort = effort
        self.allowed_tries = allowed_tries

    def _command(self) -> list[str]:
        cmd = [
            os.getenv("CLAUDE_BIN", "claude"), "-p",
            "--output-format", "json",
            "--model", self.model,
            "--no-session-persistence",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--system-prompt", self.system_prompt,
            "--tools", ",".join(self.tools),
        ]
        if self.tools:
            cmd += ["--allowedTools", *self.tools]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    @staticmethod
    def _env() -> dict[str, str]:
        state = os.getenv("STATE_DIR", os.path.expanduser("~/.metaculus-bot"))
        env = {
            "HOME": os.environ.get("HOME", ""),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "en_US.UTF-8",
            "CLAUDE_CONFIG_DIR": os.getenv("CLAUDE_CONFIG_DIR", os.path.join(state, "claude-config")),
            "CLAUDE_CODE_OAUTH_TOKEN": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""),
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
        }
        os.makedirs(env["CLAUDE_CONFIG_DIR"], exist_ok=True)
        return env

    async def _run_once(self, prompt: str, tag: str) -> str:
        state = os.getenv("STATE_DIR", os.path.expanduser("~/.metaculus-bot"))
        workdir = os.getenv("CLAUDE_WORKDIR", os.path.join(state, "claude-work"))
        os.makedirs(workdir, exist_ok=True)
        start = time.monotonic()
        row = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tag": tag,
               "role": self.role, "model": self.model, "ok": 0}
        proc = await asyncio.create_subprocess_exec(
            *self._command(), cwd=workdir, env=self._env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            row["seconds"] = round(time.monotonic() - start)
            _log_usage(row)
            usage.add(self.role, False, 0, 0, 0.0)
            raise TimeoutError(f"claude -p ({self.role}, {self.model}) timed out after {self.timeout}s")
        row["seconds"] = round(time.monotonic() - start)
        try:
            data = json.loads(out.decode() or "{}")
        except json.JSONDecodeError:
            data = {}
        u = data.get("usage") or {}
        row.update(
            turns=data.get("num_turns", ""),
            input_tokens=u.get("input_tokens", 0),
            cache_read_tokens=u.get("cache_read_input_tokens", 0),
            cache_write_tokens=u.get("cache_creation_input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            notional_usd=round(float(data.get("total_cost_usd") or 0), 4),
        )
        ok = proc.returncode == 0 and not data.get("is_error") and bool(data.get("result"))
        row["ok"] = int(ok)
        _log_usage(row)
        inp = int(row["input_tokens"] or 0) + int(row["cache_read_tokens"] or 0) + int(row["cache_write_tokens"] or 0)
        usage.add(self.role, ok, inp, int(row["output_tokens"] or 0), float(row["notional_usd"] or 0))
        if not ok:
            detail = data.get("result") or err.decode(errors="replace")[-500:]
            raise RuntimeError(f"claude -p ({self.role}) failed, exit {proc.returncode}: {detail}")
        return data["result"]

    async def invoke(self, prompt: str, tag: str = "") -> str:
        last: Exception | None = None
        for attempt in range(self.allowed_tries):
            try:
                return await self._run_once(prompt, tag)
            except Exception as e:  # noqa: BLE001 - retried, then re-raised
                last = e
                logger.warning(f"{e} (attempt {attempt + 1}/{self.allowed_tries})")
                await asyncio.sleep(10)
        assert last is not None
        raise last
