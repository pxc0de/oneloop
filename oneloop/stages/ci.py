"""CI: wait for the PR's checks. Red goes back to BUILD with the failing logs.
Zero checks is couldn't-run, never green, unless the repo says ci.mode = "none"."""

from __future__ import annotations

import os
import time

from oneloop.context import RunContext

APPEAR_GRACE_S = 120


def poll_interval() -> float:
    return float(os.environ.get("ONELOOP_CI_POLL_S", "20"))


def run(ctx: RunContext) -> str:
    s = ctx.state
    attempt = len([r for r in s.stages if r.phase == "ci"]) + 1
    with ctx.stage("ci", attempt) as rec:
        started = time.time()
        deadline = started + 60 * float(ctx.cfg["ci"]["timeout_min"])
        try:
            while True:
                pr = ctx.gh.pr(s.pr_number)
                if pr.head_sha != s.head_sha:
                    ctx.escalate("changed_under_us",
                                 f"the PR head moved to {pr.head_sha[:7]} while waiting for CI; someone else pushed")
                checks = ctx.gh.checks(s.pr_number)
                waited = time.time() - started
                if not checks:
                    if waited > min(APPEAR_GRACE_S, deadline - started):
                        ctx.escalate("couldnt_run", "CI reported no checks for this PR",
                                     'If this repo has no CI, set `ci.mode = "none"` in .oneloop.toml on the base branch.')
                elif not any(c.bucket == "pending" for c in checks):
                    break
                if time.time() > deadline:
                    ctx.escalate("couldnt_run", f"CI did not finish within {ctx.cfg['ci']['timeout_min']} minutes")
                pending = sum(c.bucket == "pending" for c in checks)
                ctx.live(f"waiting on CI: {pending} of {len(checks)} pending" if checks else "waiting for CI to start")
                time.sleep(poll_interval())
        finally:
            s.ci_wait_s += time.time() - started

        failed = [c for c in checks if c.bucket in ("fail", "cancel")]
        ctx.write_artifact(f"ci-{attempt}.json", {"sha": s.head_sha, "checks": [c.__dict__ for c in checks]})
        rec.status = "fail" if failed else "pass"
        if not failed:
            return "land"
        s.rounds["ci"] += 1
        cap = ctx.cfg["caps"]["ci_rounds"]
        names = ", ".join(c.name for c in failed)
        if s.rounds["ci"] > cap:
            ctx.escalate("cap_reached", f"CI still fails after {cap} fix rounds: {names}")
        ctx.add_feedback("ci", [{
            "summary": f"CI check `{c.name}` failed",
            "detail": "```\n" + ctx.gh.failed_log(c) + "\n```",
        } for c in failed[:3]])
    return "build"
