"""The repo's own cheap checks (lint, typecheck, test, build), run by the
orchestrator itself. The loop never takes an agent's word that tests pass.

Every check ends in one of three outcomes. 'couldnt_run' (command missing,
timed out, crashed before running) is never counted as a pass.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

OUTCOMES = ("pass", "fail", "couldnt_run", "skipped")


@dataclass
class CheckResult:
    name: str
    command: str
    outcome: str
    exit_code: int | None
    duration_s: float
    output_tail: str

    def to_json(self) -> dict:
        return asdict(self)


def tail(text: str, lines: int = 80) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_check(name: str, command: str, cwd: str | Path, timeout_s: int) -> CheckResult:
    if not command.strip():
        return CheckResult(name, "", "skipped", None, 0.0, "")
    start = time.time()
    try:
        p = subprocess.run(
            command, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return CheckResult(name, command, "couldnt_run", None, time.time() - start,
                           tail(out) + f"\n[oneloop] timed out after {timeout_s}s")
    output = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    # 126: not executable, 127: command not found. The check never ran.
    outcome = "pass" if p.returncode == 0 else ("couldnt_run" if p.returncode in (126, 127) else "fail")
    return CheckResult(name, command, outcome, p.returncode, round(time.time() - start, 2), tail(output))


def run_all(commands: dict[str, str], cwd: str | Path, timeout_s: int,
            only: list[str] | None = None) -> list[CheckResult]:
    results = []
    for name, cmd in commands.items():
        if only is not None and name not in only:
            continue
        r = run_check(name, cmd, cwd, timeout_s)
        results.append(r)
        if r.outcome in ("fail", "couldnt_run"):
            break  # cheap checks first: no point running the build on a failing lint
    return results


def summarize(results: list[CheckResult]) -> str:
    if any(r.outcome == "couldnt_run" for r in results):
        return "couldnt_run"
    if any(r.outcome == "fail" for r in results):
        return "fail"
    return "pass"
