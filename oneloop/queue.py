"""Queue mode: pick up labeled issues across repos, claim them and run each in
its own process, at most N at a time. Issues are found by their labels (the
state lives on GitHub), not by a time window, so a late or crashed run never
skips or repeats an issue."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

from oneloop import control, paths
from oneloop.github import GhCli, detect_repo
from oneloop.rules import load as load_rules
from oneloop.state import all_runs, liveness, load

BUSY_LABELS = ("oneloop:claimed", "oneloop:needs-human", "oneloop:pr")


def spawn(args: list[str], log_name: str) -> subprocess.Popen:
    logs = paths.home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    out = open(logs / log_name, "a")
    return subprocess.Popen(
        [sys.executable, "-m", "oneloop", *args], stdout=out, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )


def active_count() -> int:
    return sum(1 for s in all_runs() if liveness(s) == "active")


def _new_human_reply(gh: GhCli, issue: int, since: float) -> bool:
    for c in gh.comments(issue):
        if c.is_bot or "<!-- oneloop:" in c.body:
            continue
        try:
            ts = dt.datetime.fromisoformat(c.created_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if ts > since:
            return True
    return False


def tick(repos: dict[str, str], jobs: int, log=print) -> int:
    """One pass over every repo. Returns how many runs were started."""
    started = 0
    if control.read()["paused"]:
        log("paused: not starting new runs")
        return 0
    for repo, path in repos.items():
        gh = GhCli(repo)
        # 1. Runs that died mid-flight: resume them.
        for s in all_runs():
            if s.repo == repo and liveness(s) == "stale" and active_count() < jobs:
                log(f"{repo}#{s.issue}: process died, resuming")
                spawn(["resume", str(s.issue), "--repo", repo], f"{paths.repo_key(repo)}-{s.issue}.log")
                started += 1
        # 2. Spec questions that got an answer: resume.
        for s in all_runs():
            if (s.repo == repo and s.status == "needs_human" and s.needs_human.kind == "spec_questions"
                    and _new_human_reply(gh, s.issue, s.needs_human.since) and active_count() < jobs):
                log(f"{repo}#{s.issue}: question answered, resuming")
                spawn(["resume", str(s.issue), "--repo", repo], f"{paths.repo_key(repo)}-{s.issue}.log")
                started += 1
        # 3. New labeled issues.
        try:
            _, base = detect_repo(path)
            trigger = load_rules(path, base).config["labels"]["trigger"]
            issues = gh.list_issues(trigger)
        except Exception as e:  # one broken repo must not stop the others
            log(f"{repo}: cannot list issues: {e}")
            continue
        for iss in issues:
            if active_count() >= jobs:
                return started
            if any(l in iss.labels for l in BUSY_LABELS) or load(repo, iss.number):
                continue
            log(f"{repo}#{iss.number}: starting ({iss.title})")
            spawn(["run", str(iss.number), "--repo", path, "--queued"],
                  f"{paths.repo_key(repo)}-{iss.number}.log")
            started += 1
            time.sleep(2)  # let the child take its lock before counting again
    return started
