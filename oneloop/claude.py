"""Runs one Claude Code session headlessly (`claude -p`) and returns what it
produced, what it cost and why it stopped.

Each stage gets its own tool profile. Read-only stages get no Edit/Write.
No stage may commit, push, rewrite history or call `gh`: the orchestrator owns
every write to git history and to GitHub. These deny rules match command text,
so they are a guard rail, not a sandbox; the orchestrator also checks the
worktree and the remote after each session.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ALWAYS_DENIED = [
    "Bash(git push:*)", "Bash(git commit:*)", "Bash(git reset:*)", "Bash(git rebase:*)",
    "Bash(git checkout:*)", "Bash(git switch:*)", "Bash(git merge:*)", "Bash(git branch:*)",
    "Bash(git tag:*)", "Bash(git worktree:*)", "Bash(git config:*)", "Bash(git stash:*)",
    "Bash(git remote:*)", "Bash(git clean:*)", "Bash(gh:*)",
]


@dataclass(frozen=True)
class Profile:
    tools: tuple[str, ...]
    allowed: tuple[str, ...]
    writes: bool


PROFILES = {
    # SPEC reads the repo to understand the issue. No edits.
    "spec": Profile(tools=("Bash", "Read"), allowed=("Bash", "Read"), writes=False),
    # BUILD edits code and runs whatever the repo needs, except history and GitHub.
    "build": Profile(
        tools=("Bash", "Read", "Edit", "Write", "Skill", "Task"),
        allowed=("Bash", "Read", "Edit", "Write", "Skill", "Task"),
        writes=True,
    ),
    # VERIFY runs the real thing and records evidence outside the worktree.
    "verify": Profile(tools=("Bash", "Read", "Skill"), allowed=("Bash", "Read", "Skill"), writes=False),
    "review": Profile(tools=("Bash", "Read"), allowed=("Bash", "Read"), writes=False),
    "validate": Profile(tools=("Bash", "Read"), allowed=("Bash", "Read"), writes=False),
    "brief": Profile(tools=("Read",), allowed=("Read",), writes=False),
    "learn": Profile(tools=("Read",), allowed=("Read",), writes=False),
    "init": Profile(
        tools=("Bash", "Read", "Edit", "Write", "Skill"),
        allowed=("Bash", "Read", "Edit", "Write", "Skill"),
        writes=True,
    ),
}


@dataclass
class SessionResult:
    session_id: str
    ok: bool
    subtype: str  # success | error_max_budget_usd | error_max_turns | error_during_execution | no_result
    structured: Any = None
    text: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    turns: int = 0
    denials: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    log_path: str = ""

    @property
    def budget_reached(self) -> bool:
        return self.subtype == "error_max_budget_usd"


def claude_bin() -> str:
    return os.environ.get("ONELOOP_CLAUDE_BIN", "claude")


def describe_tool_use(name: str, inp: dict[str, Any]) -> str:
    if name == "Bash":
        lines = (inp.get("command") or "").splitlines()
        return f"Bash `{lines[0][:120] if lines else ''}`"
    if name in ("Read", "Edit", "Write"):
        path = inp.get("file_path") or ""
        return f"{name} {path.split('/worktrees/')[-1]}"
    if name == "Skill":
        return f"Skill {inp.get('skill') or inp.get('name') or ''}"
    if name == "Task":
        return f"Subagent: {inp.get('description') or ''}"
    if name == "StructuredOutput":
        return "Writing structured result"
    return name


def run(
    *,
    stage: str,
    prompt: str,
    cwd: str | Path,
    model: str,
    budget_usd: float,
    log_path: Path,
    schema: dict[str, Any] | None = None,
    resume: str | None = None,
    extra_allowed: list[str] | None = None,
    extra_denied: list[str] | None = None,
    allow_web: bool = False,
    add_dirs: list[str] | None = None,
    settings: dict[str, Any] | None = None,
    system_append: str | None = None,
    chrome: bool = False,
    env: dict[str, str] | None = None,
    on_activity: Callable[[str], None] | None = None,
    timeout_s: float = 3 * 3600,
) -> SessionResult:
    profile = PROFILES[stage]
    tools = list(profile.tools) + (["WebFetch", "WebSearch"] if allow_web else [])
    allowed = list(profile.allowed) + (["WebFetch", "WebSearch"] if allow_web else []) + (extra_allowed or [])
    denied = ALWAYS_DENIED + (extra_denied or [])
    if not profile.writes:
        denied += ["Edit", "Write", "NotebookEdit"]

    session_id = resume or str(uuid.uuid4())
    argv = [
        claude_bin(), "-p",
        "--output-format", "stream-json", "--verbose",
        "--model", model,
        "--max-budget-usd", f"{budget_usd:.2f}",
        "--permission-mode", "dontAsk",
        "--tools", ",".join(tools),
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(denied),
        "--strict-mcp-config",
    ]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema)]
    for d in add_dirs or []:
        argv += ["--add-dir", d]
    if settings is not None:
        argv += ["--settings", json.dumps(settings)]
    if system_append:
        argv += ["--append-system-prompt", system_append]
    if chrome:
        argv += ["--chrome"]

    log_path.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] | None = None
    last_text = ""
    started = time.time()
    proc_env = {**os.environ, **(env or {})}
    with open(log_path, "a") as log:
        log.write(json.dumps({"type": "oneloop.session_start", "stage": stage, "model": model,
                              "resume": bool(resume), "session_id": session_id,
                              "ts": started}) + "\n")
        proc = subprocess.Popen(
            argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=proc_env,
        )
        assert proc.stdin and proc.stdout
        proc.stdin.write(prompt)
        proc.stdin.close()
        last_emit = 0.0
        for line in proc.stdout:
            log.write(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "result":
                result = msg
            elif msg.get("type") == "assistant":
                for block in (msg.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use" and on_activity:
                        now = time.time()
                        if now - last_emit > 0.5:
                            on_activity(describe_tool_use(block.get("name", ""), block.get("input") or {}))
                            last_emit = now
                    elif block.get("type") == "text":
                        last_text = block.get("text") or last_text
            if time.time() - started > timeout_s:
                proc.kill()
                break
        stderr = proc.stderr.read() if proc.stderr else ""
        proc.wait()

    if result is None:
        return SessionResult(
            session_id=session_id, ok=False, subtype="no_result", text=last_text,
            error=(stderr.strip() or f"claude exited {proc.returncode} without a result")[-2000:],
            log_path=str(log_path),
        )
    subtype = result.get("subtype", "")
    structured = result.get("structured_output")
    ok = subtype == "success" and not result.get("is_error")
    if ok and schema is not None and structured is None:
        ok, subtype = False, "no_structured_output"
    return SessionResult(
        session_id=result.get("session_id") or session_id,
        ok=ok,
        subtype=subtype,
        structured=structured,
        text=result.get("result") or last_text or "",
        cost_usd=float(result.get("total_cost_usd") or 0.0),
        duration_ms=int(result.get("duration_ms") or 0),
        turns=int(result.get("num_turns") or 0),
        denials=result.get("permission_denials") or [],
        error="; ".join(result.get("errors") or []) or (stderr.strip()[-1000:] if not ok else ""),
        log_path=str(log_path),
    )
