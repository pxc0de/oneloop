"""VERIFY: a fresh session that never saw the builder's notes runs the real
change against each acceptance criterion and records evidence.

The orchestrator, not the verifier, decides the outcome: every spec criterion
must be graded, any fail is a fail, any couldn't-run is couldn't-run. A
verifier that modifies the worktree has its result thrown away.
"""

from __future__ import annotations

from typing import Any

from oneloop import gitops, render, schemas
from oneloop.context import RunContext
from oneloop.prompts import render as render_prompt


def aggregate(spec_criteria: list[dict[str, Any]], graded: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    by_id = {g["id"]: g for g in graded}
    rows = []
    for c in spec_criteria:
        g = by_id.get(c["id"])
        if g is None:
            g = {"id": c["id"], "outcome": "couldnt_run", "how": "", "observed": "not graded by the verifier",
                 "evidence_files": []}
        rows.append({**g, "description": c["description"]})
    outcomes = {r["outcome"] for r in rows}
    if "fail" in outcomes:
        overall = "fail"
    elif "couldnt_run" in outcomes or not rows:
        overall = "couldnt_run"
    else:
        overall = "pass"
    return overall, rows


def run(ctx: RunContext) -> str:
    s = ctx.state
    gate = ctx.cfg.gate("verify")
    if gate == "off":
        ctx.emit("gate.off", gate="verify")
        s.verified_sha = s.head_sha
        return "review"
    attempt = len([r for r in s.stages if r.phase == "verify"]) + 1
    with ctx.stage("verify", attempt) as rec:
        ctx.refresh_rules()
        spec = ctx.read_artifact("spec.json")
        evidence = ctx.dir / "evidence" / f"v{attempt}"
        evidence.mkdir(parents=True, exist_ok=True)
        before = gitops.status_porcelain(ctx.wt)
        prompt = render_prompt(
            "verify", guidance=ctx.rules.guidance_for("verify"), sha=s.head_sha or "",
            criteria=render.criteria_md(spec["acceptance_criteria"]),
            verify_skill=ctx.rules.verify_skill or (
                "_This repository has no verify skill yet._ Work out how to build and run it from its "
                "README, manifest and scripts. Prefer exercising the real interface (CLI, HTTP, UI) "
                "over reading code. Run `oneloop init` later to give it one."),
            evidence_dir=str(evidence),
        )
        res = ctx.session("verify", prompt, schema=schemas.VERIFY, add_dirs=[str(evidence)],
                          chrome=bool(ctx.cfg["verify"]["chrome"]),
                          extra_allowed=list(ctx.cfg["verify"]["allowed"]),
                          timeout_s=float(ctx.cfg["verify"]["timeout_min"]) * 60)
        after = gitops.status_porcelain(ctx.wt)
        tampered = after != before
        if tampered:
            gitops.discard_changes(ctx.wt)

        if tampered:
            overall, rows, reason = "couldnt_run", [], "the verifier modified files in the worktree; result discarded"
        elif not res.ok:
            overall, rows, reason = "couldnt_run", [], f"verify session ended with {res.subtype}: {res.error}"
        else:
            overall, rows = aggregate(spec["acceptance_criteria"], res.structured["criteria"])
            reason = res.structured.get("couldnt_run_reason") or ""
        result = {
            "attempt": attempt, "sha": s.head_sha, "outcome": overall, "criteria": rows,
            "couldnt_run_reason": reason, "notes": (res.structured or {}).get("notes", ""),
            "evidence_dir": str(evidence),
        }
        ctx.write_artifact(f"verify-{attempt}.json", result)
        ctx.write_artifact("verify-latest.json", result)
        rec.status = overall
        ctx.emit("verify", outcome=overall, passed=sum(r["outcome"] == "pass" for r in rows), total=len(rows))

        if overall != "pass" and gate == "observe":
            ctx.emit("gate.observe", gate="verify", would_block=overall)
            overall = "pass"
        if overall == "pass":
            s.verified_sha = s.head_sha
            return "review"
        if overall == "couldnt_run":
            not_run = [r for r in rows if r["outcome"] == "couldnt_run"]
            ctx.escalate("couldnt_run", f"verify could not run: {reason or 'criteria not exercised'}",
                         "\n".join(f"- {r['id']}: {r['observed']}" for r in not_run))
        s.rounds["verify"] += 1
        cap = ctx.cfg["caps"]["verify_rounds"]
        failed = [r for r in rows if r["outcome"] == "fail"]
        if s.rounds["verify"] > cap:
            ctx.escalate("cap_reached", f"{len(failed)} criteria still fail verification after {cap} fix rounds",
                         "\n".join(f"- {r['id']}: {r['observed']}" for r in failed))
        ctx.add_feedback("verify", [{
            "summary": f"{r['id']} fails: {r['description']}",
            "detail": f"Verifier ran: {r['how']}\n\nObserved: {r['observed']}",
        } for r in failed])
    return "build"
