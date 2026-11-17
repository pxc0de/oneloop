"""LAND: re-check live state, then merge or hand a brief to a person.

Right before merging, everything is read again: the PR head must still be the
commit that was verified and reviewed, CI must be green now, and nobody may
have commented, requested changes, put a hold on it or closed the issue. If
the base moved, the branch is rebased and goes back through checks and
verify; a review carries over only if the change's patch-id is unchanged.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from oneloop import control, gitops, ledger, render, schemas
from oneloop.context import RunContext
from oneloop.effects import perform
from oneloop.prompts import render as render_prompt
from oneloop.risk import rank


def _ts(iso: str) -> float:
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def auto_merge_decision(ctx: RunContext) -> tuple[bool, str]:
    """(may merge without a person, why not)."""
    s = ctx.state
    level = (s.risk or {}).get("level", "high")
    if s.approved_sha and s.approved_sha == s.head_sha:
        return True, ""
    setting = ctx.cfg["land"]["auto_merge"]
    ctl = control.read()
    if ctl["paused"]:
        return False, "oneloop is paused, so nothing merges without a person"
    if ctl["auto_merge"].get(s.repo) is False:
        return False, f"auto-merge is switched off for {s.repo}"
    if setting == "off":
        return False, "auto-merge is off in .oneloop.toml"
    if rank(level) > rank(setting):
        reasons = [r["detail"] for r in (s.risk or {}).get("reasons", [])
                   if not r.get("observed_only") and rank(r["level"]) > rank(setting)]
        return False, f"{level} risk: " + ("; ".join(reasons[:3]) or "above the auto-merge level")
    return True, ""


def make_brief(ctx: RunContext, why: str) -> str:
    s = ctx.state
    spec = ctx.read_artifact("spec.json") or {}
    verify = ctx.read_artifact("verify-latest.json")
    review = ctx.read_artifact("review-latest.json")
    diffstat = gitops.git(ctx.wt, "diff", "--stat", f"{ctx.rules.base_ref}...HEAD")
    prompt = render_prompt(
        "brief", guidance=ctx.rules.guidance_for("brief"), why_human=why, issue=str(s.issue), title=s.title,
        spec_summary=spec.get("summary", ""), verify=render.verify_table(verify),
        review=(render.findings_md((review or {}).get("findings", [])) + "\n\n" + (review or {}).get("summary", "")),
        risk=render.risk_md(s.risk), diffstat=diffstat,
    )
    res = ctx.session("brief", prompt, schema=schemas.BRIEF)
    if res.ok and res.structured:
        ctx.write_artifact("brief.json", res.structured)
        md = render.brief_md(res.structured, why)
    else:
        # Never block a decision on the brief: fall back to the facts we have.
        md = "\n\n".join([
            f"### oneloop brief: {s.title}", f"**Why you're seeing this:** {why}",
            "**Verification**\n" + render.verify_table(verify), "**Risk**\n" + render.risk_md(s.risk),
            "**Diff**\n```\n" + diffstat + "\n```",
        ])
    ctx.write_artifact("brief.md", md)
    return md


def _recheck(ctx: RunContext) -> str | None:
    """Return the next phase if something must run again, else None. Escalates
    when a person changed something."""
    s = ctx.state
    pr = ctx.gh.pr(s.pr_number)
    if pr.state == "MERGED":
        _finish_merged(ctx, by_human=True, merge_commit=pr.merge_commit)
        return "done"
    if pr.state == "CLOSED":
        s.finish("cancelled")
        ctx.emit("cancelled", reason="PR was closed")
        ledger.record(s, "cancelled")
        return "done"
    if pr.head_sha != s.head_sha:
        ctx.escalate("changed_under_us", f"the PR head is {pr.head_sha[:7]}, not the reviewed {str(s.head_sha)[:7]}")
    issue = ctx.gh.issue(s.issue)
    if "oneloop:hold" in pr.labels or "oneloop:hold" in issue.labels:
        ctx.escalate("changed_under_us", "the `oneloop:hold` label is set")
    if issue.state != "OPEN":
        ctx.escalate("changed_under_us", "the issue was closed")
    if pr.review_decision == "CHANGES_REQUESTED":
        ctx.escalate("changed_under_us", "a reviewer requested changes on the PR",
                     "Send it back with `oneloop sendback` to have the builder address it.")
    fresh = [c for c in pr.comments
             if not c.is_bot and "<!-- oneloop:" not in c.body and _ts(c.created_at) > s.acknowledged_at]
    if fresh:
        c = fresh[-1]
        ctx.escalate("changed_under_us", f"new comment on the PR from @{c.author}", c.body[:1000])

    # Base moved: rebase and go round again.
    gitops.fetch(ctx.wt, s.base)
    base = ctx.rules.base_ref
    if not gitops.is_ancestor(ctx.wt, base, "HEAD"):
        old_pid = s.reviewed_patch_id
        if not gitops.rebase(ctx.wt, base):
            return _resolve_conflict(ctx, base)
        s.head_sha = gitops.head(ctx.wt)
        s.verified_sha = None
        same = bool(old_pid) and gitops.patch_id(ctx.wt, base) == old_pid
        s.reviewed_sha = s.head_sha if same else None
        ctx.emit("rebased", head=s.head_sha, review_carried_over=same)
        return "checks"

    if s.verified_sha != s.head_sha:
        return "checks"
    if s.reviewed_sha != s.head_sha:
        return "review"
    if ctx.cfg["ci"]["mode"] == "required":
        checks = ctx.gh.checks(s.pr_number)
        if not checks or any(c.bucket in ("pending", "fail", "cancel") for c in checks):
            return "ci"
    return None


def _resolve_conflict(ctx: RunContext, base: str) -> str:
    """Another change landed on the same lines. Start the merge here and hand
    the conflicted files to the builder's own session; a person is needed only
    if that fails twice."""
    s = ctx.state
    s.rounds["conflict"] = s.rounds.get("conflict", 0) + 1
    if s.rounds["conflict"] > 2:
        ctx.escalate("conflict", f"conflicts with {base} remain after 2 attempts to resolve them",
                     f"Resolve in {ctx.wt} and resume, or close the PR.")
    files = gitops.merge_start(ctx.wt, base)
    s.verified_sha = None
    s.reviewed_sha = None  # the next review sees the whole change against the new base
    ctx.emit("conflict", files=files, attempt=s.rounds["conflict"])
    ctx.add_feedback("rebase", [{
        "summary": f"{base} changed the same code; resolve the merge conflicts in " + ", ".join(f"`{f}`" for f in files),
        "detail": (f"oneloop has started `git merge {base}` in this worktree. The files above contain conflict "
                   "markers. Resolve each conflict so both the change on the base branch and this issue's "
                   "change keep working, remove every marker, and run the checks. Do not run git commands; "
                   "oneloop commits the merge."),
    }])
    return "build"


def _finish_merged(ctx: RunContext, by_human: bool, merge_commit: str | None) -> None:
    s = ctx.state
    s.finish("merged")
    ctx.emit("merged", by="human" if by_human else ("approved" if s.approved_sha else "auto"),
             merge_commit=merge_commit)
    ledger.record(s, "merged", merged_by_human_on_github=by_human, merge_commit=merge_commit)
    for label in ("oneloop:claimed", "oneloop:needs-human"):
        ctx.unlabel(label)
    gitops.remove_worktree(s.repo_path, ctx.wt)


def run(ctx: RunContext) -> str:
    s = ctx.state
    with ctx.stage("land"):
        ctx.refresh_rules()
        nxt = _recheck(ctx)
        if nxt:
            return nxt
        ok, why = auto_merge_decision(ctx)
        if not ok:
            brief = make_brief(ctx, why)
            ctx.upsert_comment(s.pr_number, "brief", brief)
            ctx.escalate("approval", why, brief)

        verify = ctx.read_artifact("verify-latest.json")
        review = ctx.read_artifact("review-latest.json")
        checks = (ctx.read_artifact("checks-latest.json") or {}).get("results")
        stamp = render.stamp(s, verify, review, checks)
        subject = f"{s.title} (#{s.pr_number})"
        body = f"Closes #{s.issue}\n\n{stamp}"
        head = s.head_sha or ""

        def confirm() -> Any:
            p = ctx.gh.pr(s.pr_number)
            return {"merge_commit": p.merge_commit} if p.state == "MERGED" else None

        # A 'maybe' merge is confirmed first; re-acting is safe because the
        # merge is pinned to the head commit with --match-head-commit.
        res = perform(s, f"merge:{head}",
                      act=lambda: ctx.gh.merge(s.pr_number, head, ctx.cfg["land"]["merge_method"], subject, body),
                      confirm=confirm)
        _finish_merged(ctx, by_human=False, merge_commit=res.get("merge_commit"))
    return "done"
