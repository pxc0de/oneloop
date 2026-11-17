"""Where oneloop keeps state. Everything lives under one global folder so the
dashboard, the mod and the CLI see every run across every repo."""

from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("ONELOOP_HOME") or Path.home() / ".oneloop")


def repo_key(repo: str) -> str:
    """'owner/name' -> 'owner__name', safe as a single path segment."""
    return repo.replace("/", "__")


def repo_from_key(key: str) -> str:
    return key.replace("__", "/", 1)


def runs_root() -> Path:
    return home() / "runs"


def run_dir(repo: str, issue: int) -> Path:
    return runs_root() / repo_key(repo) / str(issue)


def worktree_dir(repo: str, issue: int) -> Path:
    return home() / "worktrees" / repo_key(repo) / str(issue)


def ledger_path() -> Path:
    return home() / "ledger.jsonl"


def control_path() -> Path:
    return home() / "control.json"


def repos_path() -> Path:
    return home() / "repos.json"


def package_dir() -> Path:
    return Path(__file__).resolve().parent
