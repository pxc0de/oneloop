"""SHIP: push the branch and open (or update) the PR. No model involved.
Every write is confirmed by a read before it is recorded."""

from __future__ import annotations

import hashlib

from oneloop import gitops, render
from oneloop.context import RunContext
from oneloop.effects import perform


def pr_body(ctx: RunContext) -> str:
    s = ctx.state
    builds = sorted(ctx.dir.glob("artifacts/build-*.json"))
    first_build = ctx.read_artifact(builds[0].name) if builds else None
    checks = (ctx.read_artifact("checks-latest.json") or {}).get("results")
    return render.pr_body(s, ctx.read_artifact("spec.json"), first_build,
                          ctx.read_artifact("verify-latest.json"), ctx.read_artifact("review-latest.json"), checks)


def run(ctx: RunContext) -> str:
    s = ctx.state
    with ctx.stage("ship"):
        head = gitops.head(ctx.wt)
        expect = s.pushed_sha
        perform(
            s, f"push:{head}",
            act=lambda: gitops.push(ctx.wt, s.branch, expect=expect, force=True),
            confirm=lambda: head if gitops.remote_sha(ctx.wt, s.branch) == head else None,
        )
        s.pushed_sha = head
        body = pr_body(ctx)
        existing = ctx.gh.pr_for_branch(s.branch)
        if existing is None or existing.state == "CLOSED":
            def confirm_pr():
                p = ctx.gh.pr_for_branch(s.branch)
                return {"number": p.number, "url": p.url} if p and p.state == "OPEN" else None

            pr = perform(s, f"pr:create:{s.branch}",
                         act=lambda: ctx.gh.create_pr(s.branch, s.base, f"{s.title} (#{s.issue})", body),
                         confirm=confirm_pr)
            s.pr_number, s.pr_url = pr["number"], pr["url"]
        elif existing.state == "MERGED":
            s.pr_number, s.pr_url = existing.number, existing.url
            return "land"  # someone merged it already; LAND records the outcome
        else:
            s.pr_number, s.pr_url = existing.number, existing.url
            digest = hashlib.sha256(body.encode()).hexdigest()[:10]
            perform(s, f"pr:body:{digest}",
                    act=lambda: ctx.gh.edit_pr_body(existing.number, body),
                    confirm=lambda: True if ctx.gh.pr(existing.number).body.strip() == body.strip() else None)
        ctx.label("oneloop:pr")
        ctx.emit("pr", number=s.pr_number, url=s.pr_url, head=head)
    return "ci" if ctx.cfg["ci"]["mode"] == "required" else "land"
