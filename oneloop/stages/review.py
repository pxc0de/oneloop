"""REVIEW: one reviewer per round, guided by the repo's REVIEW.md and the
checklist items the diff triggers, then a validator that keeps only findings
it can confirm. Rounds build on each other: later rounds see earlier
findings and review only the new diff. A review is pinned to one commit."""

from __future__ import annotations

from typing import Any

from oneloop import checklist, gitops, render, risk as risk_mod, schemas
from oneloop.context import RunContext
from oneloop.prompts import UNTRUSTED_NOTE, render as render_prompt, untrusted

MAX_DIFF_CHARS = 150_000


def _clip(diff: str) -> str:
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    return diff[:MAX_DIFF_CHARS] + "\n\n[diff truncated by oneloop: read the remaining files directly]\n"


def _previous(ctx: RunContext, rnd: int) -> tuple[str, list[dict[str, Any]]]:
    blocking: list[dict[str, Any]] = []
    for r in range(1, rnd):
        prev = ctx.read_artifact(f"review-{r}.json") or {}
        blocking += [f for f in prev.get("findings", []) if f["id"] in prev.get("blocking", [])]
    if not blocking:
        return "(first round)" if rnd == 1 else "No blocking findings in earlier rounds.", []
    return ("Findings that blocked earlier rounds. Report each under `previous` as resolved or not:\n\n"
            + render.findings_md(blocking)), blocking


def merge_verdicts(findings: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {v["id"]: v for v in verdicts}
    out = []
    for f in findings:
        v = by_id.get(f["id"])
        if v is None:
            out.append({**f, "verdict": "uncertain", "verdict_reason": "not covered by the validator"})
        else:
            out.append({**f, "severity": v["severity"], "verdict": v["verdict"], "verdict_reason": v["reason"]})
    return out


def blocking_findings(findings: list[dict[str, Any]], items: dict[str, checklist.Item],
                      checklist_gate: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(blocking, observed_only). Only confirmed important findings block.
    A finding tied to a checklist item in observe mode is logged instead."""
    blocking, observed = [], []
    for f in findings:
        if f["verdict"] != "confirmed" or f["severity"] != "important":
            continue
        item = items.get(f.get("checklist_item") or "")
        mode = "enforce"
        if item is not None:
            mode = "observe" if "observe" in (item.mode, checklist_gate) else item.mode
        (observed if mode == "observe" else blocking).append(f)
    return blocking, observed


def run(ctx: RunContext) -> str:
    s = ctx.state
    head = gitops.head(ctx.wt)
    if s.reviewed_sha == head:
        ctx.emit("review.carried_over", sha=head)
        return "ship"
    rnd = len([r for r in s.stages if r.phase == "review"]) + 1
    with ctx.stage("review", rnd) as rec:
        ctx.refresh_rules()
        base = ctx.rules.base_ref
        spec = ctx.read_artifact("spec.json")
        issue = ctx.gh.issue(s.issue)
        changes = gitops.changes(ctx.wt, base)
        full = gitops.diff(ctx.wt, base)
        added = gitops.added_lines(full)

        try:
            items = checklist.parse(ctx.rules.checklist_md)
        except checklist.ChecklistError as e:
            ctx.escalate("couldnt_run", f"the checklist on {base} is invalid: {e}")
        matched = checklist.match(items, [c.path for c in changes], added)
        items_by_id = {m.item.id: m.item for m in matched}
        checklist_text = "\n\n".join(
            f"**{m.item.id}: {m.item.title}** (fired by {m.because})\n{m.item.check}"
            + (f"\nOrigin: {m.item.origin}" if m.item.origin else "")
            for m in matched
        ) or "(no items triggered)"

        if rnd > 1 and s.reviewed_sha and gitops.is_ancestor(ctx.wt, s.reviewed_sha, head):
            diff = gitops.diff(ctx.wt, s.reviewed_sha, head, triple_dot=False)
            scope = (f"Only the changes since the last reviewed commit {s.reviewed_sha[:7]}. Earlier rounds "
                     f"reviewed the rest; the full change is `git diff {base}...HEAD`.")
        else:
            diff = full
            scope = f"The full change against `{base}`."
        previous_text, previous_blocking = _previous(ctx, rnd)

        prompt = render_prompt(
            "review", guidance=ctx.rules.guidance_for("review"), untrusted_note=UNTRUSTED_NOTE,
            round=str(rnd), issue=str(s.issue), title=s.title, issue_body=untrusted(issue.body or "(empty)"),
            spec=render.spec_md(spec),
            review_md=ctx.rules.review_md or "(this repo has no REVIEW.md yet)",
            checklist=checklist_text, previous=previous_text, diff_scope=scope, diff=_clip(diff),
        )
        res = ctx.session("review", prompt, schema=schemas.REVIEW)
        if not res.ok:
            ctx.escalate("couldnt_run", f"review session ended with {res.subtype}", res.error)
        out = res.structured
        findings = out["findings"]

        # Earlier blocking findings the reviewer says are still open block again.
        unresolved = {p["id"] for p in out.get("previous", []) if not p["resolved"]}
        carried = [{**f, "id": f"{f['id']}~r{rnd}", "summary": "(still open) " + f["summary"]}
                   for f in previous_blocking if f["id"] in unresolved]
        findings = findings + carried

        if findings:
            scratch = ctx.dir / "evidence" / f"validate{rnd}"
            scratch.mkdir(parents=True, exist_ok=True)
            vprompt = render_prompt(
                "validate", guidance=ctx.rules.guidance_for("validate"), scratch_dir=str(scratch),
                base=base, findings=render.findings_md(findings),
                criteria=render.criteria_md(spec["acceptance_criteria"]),
            )
            before = gitops.status_porcelain(ctx.wt)
            vres = ctx.session("validate", vprompt, schema=schemas.VALIDATE, add_dirs=[str(scratch)])
            if gitops.status_porcelain(ctx.wt) != before:
                gitops.discard_changes(ctx.wt)
                ctx.escalate("couldnt_run", "the validator modified files in the worktree; result discarded")
            if not vres.ok:
                ctx.escalate("couldnt_run", f"validate session ended with {vres.subtype}", vres.error)
            findings = merge_verdicts(findings, vres.structured["verdicts"])
        blocking, observed = blocking_findings(findings, items_by_id, ctx.cfg.gate("checklist"))
        for f in observed:
            ctx.emit("gate.observe", gate="checklist", item=f.get("checklist_item"), finding=f["id"])

        risk = risk_mod.assess(changes, added, ctx.cfg)
        risk.add("reviewer", out["risk"]["level"], f"reviewer: {out['risk']['reason']}")
        uncertain = [f for f in findings if f.get("verdict") == "uncertain" and f["severity"] == "important"]
        if uncertain:
            risk.add("uncertain_findings", "medium",
                     f"{len(uncertain)} important finding(s) the validator could not confirm or rule out")
        s.risk = risk.to_json()

        result = {
            "round": rnd, "sha": head, "summary": out["summary"], "findings": findings,
            "blocking": [f["id"] for f in blocking], "checklist_matched": [
                {"id": m.item.id, "title": m.item.title, "because": m.because, "mode": m.item.mode}
                for m in matched],
            "checklist_results": out.get("checklist", []), "previous": out.get("previous", []),
            "reviewer_risk": out["risk"],
        }
        ctx.write_artifact(f"review-{rnd}.json", result)
        ctx.write_artifact("review-latest.json", result)
        s.reviewed_sha = head
        s.reviewed_patch_id = gitops.patch_id(ctx.wt, base)
        ctx.emit("review", round=rnd, findings=len(findings), blocking=len(blocking),
                 rejected=sum(f.get("verdict") == "rejected" for f in findings), risk=risk.level)
        rec.status = "fail" if blocking else "pass"

        if not blocking:
            return "ship"
        s.rounds["review"] += 1
        cap = ctx.cfg["caps"]["review_rounds"]
        if s.rounds["review"] > cap:
            ctx.escalate("cap_reached", f"{len(blocking)} important finding(s) remain after {cap} review rounds",
                         render.findings_md(blocking))
        ctx.add_feedback("review", [{
            "summary": f"[{f['id']}] `{f['file']}:{f['line']}` {f['summary']}",
            "detail": f"Failure scenario: {f['failure_scenario']}\n\nValidator: {f.get('verdict_reason', '')}",
        } for f in blocking])
    return "build"
