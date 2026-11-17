"""Run state: one JSON document per (repo, issue), plus an append-only event log.

The state file is the single source of truth for where a run is. Every write
is atomic (write to a temp file, then rename), so a crash leaves either the old
or the new state, never half of one.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from oneloop import paths

# Order matters: it is the happy path through the loop.
PHASES = ("spec", "build", "checks", "verify", "review", "ship", "ci", "land")

# running      the loop is (or should be) working on it
# needs_human  stopped until a person acts; see RunState.needs_human
# merged       done
# cancelled    a person stopped it
# failed       oneloop itself hit an internal error; see the event log
STATUSES = ("running", "needs_human", "merged", "cancelled", "failed")
TERMINAL = ("merged", "cancelled")

# Why a run needs a person. The dashboard groups the inbox by these.
NEEDS_HUMAN_KINDS = (
    "spec_questions",     # the issue is ambiguous; questions posted on it
    "approval",           # medium/high risk, or auto-merge is off
    "couldnt_run",        # a check could not run; never counted as a pass
    "cap_reached",        # a fix loop hit its round cap
    "budget_reached",     # a session hit its spending cap
    "builder_blocked",    # the builder says it cannot proceed
    "conflict",           # the branch no longer rebases cleanly onto base
    "changed_under_us",   # someone pushed, commented or put a hold on the PR
    "uncertain_effect",   # a GitHub write could not be confirmed either way
    "paused",             # global pause or auto-merge turned off mid-run
    "error",              # internal error
)


def now() -> float:
    return time.time()


@dataclass
class NeedsHuman:
    kind: str
    reason: str
    detail: str = ""
    since: float = field(default_factory=now)

    def __post_init__(self) -> None:
        if self.kind not in NEEDS_HUMAN_KINDS:
            raise ValueError(f"unknown needs_human kind {self.kind!r}")


@dataclass
class StageRecord:
    phase: str
    round: int
    started: float
    ended: float | None = None
    status: str = "running"  # running|pass|fail|couldnt_run|done|error
    cost_usd: float = 0.0
    sessions: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class RunState:
    repo: str
    issue: int
    repo_path: str
    title: str = ""
    base: str = "main"
    branch: str = ""
    worktree: str = ""
    phase: str = "spec"
    status: str = "running"
    needs_human: NeedsHuman | None = None
    rounds: dict[str, int] = field(
        default_factory=lambda: {"build": 0, "checks": 0, "verify": 0, "review": 0, "ci": 0}
    )
    build_session: str | None = None
    start_sha: str | None = None  # base commit the branch started from
    # Last time a person acted on this run; PR comments older than this are already seen.
    acknowledged_at: float = 0.0
    # Work items for the next BUILD round: [{source, summary, detail}]
    feedback: list[dict[str, Any]] = field(default_factory=list)
    head_sha: str | None = None
    verified_sha: str | None = None
    reviewed_sha: str | None = None
    reviewed_patch_id: str | None = None
    approved_sha: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    risk: dict[str, Any] | None = None
    costs: dict[str, float] = field(default_factory=dict)
    stages: list[StageRecord] = field(default_factory=list)
    # Side effects on GitHub: key -> {status: intent|confirmed|maybe, result, at}
    effects: dict[str, dict[str, Any]] = field(default_factory=dict)
    human_touches: int = 0
    human_wait_s: float = 0.0
    ci_wait_s: float = 0.0
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"unknown phase {self.phase!r}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if (self.status == "needs_human") != (self.needs_human is not None):
            raise ValueError("needs_human must be set exactly when status is needs_human")

    @property
    def dir(self) -> Path:
        return paths.run_dir(self.repo, self.issue)

    @property
    def total_cost(self) -> float:
        return round(sum(self.costs.values()), 4)

    def add_cost(self, phase: str, usd: float) -> None:
        self.costs[phase] = round(self.costs.get(phase, 0.0) + usd, 6)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "RunState":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        if data.get("needs_human"):
            data["needs_human"] = NeedsHuman(**data["needs_human"])
        data["stages"] = [StageRecord(**s) for s in data.get("stages", [])]
        return cls(**data)

    # --- transitions -----------------------------------------------------
    def to_phase(self, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        self.phase = phase

    def ask_human(self, kind: str, reason: str, detail: str = "") -> None:
        self.status = "needs_human"
        self.needs_human = NeedsHuman(kind=kind, reason=reason, detail=detail)

    def resume(self, touched: bool = True) -> None:
        """Back to running after a person acted."""
        if self.needs_human is not None:
            self.human_wait_s += max(0.0, now() - self.needs_human.since)
        self.status = "running"
        self.needs_human = None
        self.acknowledged_at = now()
        if touched:
            self.human_touches += 1

    def finish(self, status: str) -> None:
        if status not in TERMINAL:
            raise ValueError(f"{status!r} is not a terminal status")
        self.status = status
        self.needs_human = None


# --- persistence ---------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_json(path: Path, data: Any) -> None:
    _atomic_write(path, json.dumps(data, indent=2, sort_keys=False) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def save(state: RunState) -> None:
    state.updated_at = now()
    write_json(state.dir / "state.json", state.to_json())


def load(repo: str, issue: int) -> RunState | None:
    data = read_json(paths.run_dir(repo, issue) / "state.json")
    return RunState.from_json(data) if data else None


def all_runs() -> list[RunState]:
    out = []
    root = paths.runs_root()
    if not root.exists():
        return out
    for f in sorted(root.glob("*/*/state.json")):
        try:
            out.append(RunState.from_json(json.loads(f.read_text())))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return out


def emit(state: RunState, type_: str, **data: Any) -> None:
    """Append one event to the run's log. The dashboard tails this file."""
    state.dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": now(), "type": type_, "phase": state.phase, **data}
    with open(state.dir / "events.jsonl", "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    try:
        lines = (run_dir / "events.jsonl").read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# --- process lock ----------------------------------------------------------

def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLocked(RuntimeError):
    pass


class RunLock:
    """One oneloop process per run. A lock left by a dead process is stale and
    gets taken over; the dashboard shows such runs as died, not idle."""

    def __init__(self, run_dir: Path):
        self.path = run_dir / "lock"

    def holder(self) -> int | None:
        try:
            pid = int(self.path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None
        return pid if pid_alive(pid) else None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        holder = self.holder()
        if holder is not None and holder != os.getpid():
            raise RunLocked(f"run is already being worked on by pid {holder}")
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self.path.read_text().strip() == str(os.getpid()):
                self.path.unlink()
        except FileNotFoundError:
            pass


def liveness(state: RunState) -> str:
    """'active' (a process holds the lock), 'stale' (running but nobody is
    working on it: the process died), or 'idle' (not running)."""
    if state.status != "running":
        return "idle"
    return "active" if RunLock(state.dir).holder() else "stale"
