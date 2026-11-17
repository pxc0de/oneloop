"""CHECKS: the repo's lint/typecheck/test/build, run by the orchestrator.
Cheap checks first, so the expensive stages never see code that fails them."""

from __future__ import annotations

from oneloop import checks
from oneloop.context import RunContext


def run(ctx: RunContext) -> str:
    s = ctx.state
    attempt = len([r for r in s.stages if r.phase == "checks"]) + 1
    with ctx.stage("checks", attempt) as rec:
        results = checks.run_all(ctx.cfg.check_commands(), ctx.wt, int(ctx.cfg["checks"]["timeout_s"]))
        ctx.write_artifact(f"checks-{attempt}.json", {"sha": s.head_sha, "results": [r.to_json() for r in results]})
        ctx.write_artifact("checks-latest.json", {"sha": s.head_sha, "results": [r.to_json() for r in results]})
        outcome = checks.summarize(results)
        rec.status = outcome
        if all(r.outcome == "skipped" for r in results):
            rec.note = "no checks configured"
        if outcome == "pass":
            return "verify"
        bad = next(r for r in results if r.outcome in ("fail", "couldnt_run"))
        if outcome == "couldnt_run":
            ctx.escalate("couldnt_run", f"check '{bad.name}' could not run (`{bad.command}`)", bad.output_tail)
        s.rounds["checks"] += 1
        cap = ctx.cfg["caps"]["checks_rounds"]
        if s.rounds["checks"] > cap:
            ctx.escalate("cap_reached", f"`{bad.command}` still fails after {cap} fix rounds", bad.output_tail)
        ctx.add_feedback("checks", [{
            "summary": f"`{bad.command}` fails (exit {bad.exit_code})",
            "detail": "```\n" + bad.output_tail + "\n```",
        }])
    return "build"
