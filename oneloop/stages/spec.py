"""SPEC: issue -> acceptance criteria, plan and test strategy. The contract
every later stage checks against. Asks on the issue instead of guessing when
an open decision would change what gets built."""

from __future__ import annotations

from oneloop import render, schemas
from oneloop.context import RunContext, Stop
from oneloop.github import MARKER
from oneloop.prompts import UNTRUSTED_NOTE, render as render_prompt, untrusted


def run(ctx: RunContext) -> str:
    s = ctx.state
    with ctx.stage("spec"):
        ctx.refresh_rules()
        issue = ctx.gh.issue(s.issue)
        if issue.state != "OPEN":
            s.finish("cancelled")
            ctx.emit("cancelled", reason="issue is closed")
            raise Stop("issue closed")
        s.title = issue.title

        comments = []
        for c in issue.comments:
            if c.is_bot:
                continue
            ours = "<!-- oneloop:" in c.body
            who = "oneloop (earlier questions)" if ours else f"@{c.author}"
            body = c.body.replace(MARKER.format(key="spec-questions"), "").strip()
            comments.append(f"{who} at {c.created_at}:\n" + (body if ours else untrusted(body)))

        prompt = render_prompt(
            "spec",
            guidance=ctx.rules.guidance_for("spec"),
            untrusted_note=UNTRUSTED_NOTE,
            repo=s.repo, issue=str(s.issue), title=issue.title,
            issue_body=untrusted(issue.body or "(empty)"),
            comments="\n\n".join(comments) or "(none)",
        )
        res = ctx.session("spec", prompt, schema=schemas.SPEC)
        if not res.ok:
            ctx.escalate("couldnt_run", f"spec session ended with {res.subtype}", res.error)
        spec = res.structured
        ctx.write_artifact("spec.json", spec)
        ctx.write_artifact("spec.md", render.spec_md(spec))
        ctx.emit("spec", ready=spec["ready"], criteria=len(spec["acceptance_criteria"]),
                 risk_guess=spec["risk_guess"])

        if not spec["ready"]:
            qs = spec.get("questions") or ["(the spec session gave no questions)"]
            body = (
                "Before building this, oneloop needs a decision on the following. "
                "Reply in a comment; the loop picks it up from there.\n\n"
                + "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1))
            )
            ctx.upsert_comment(s.issue, "spec-questions", body)
            ctx.escalate("spec_questions", f"{len(qs)} question(s) posted on the issue", "\n".join(qs))
        if not spec["acceptance_criteria"]:
            ctx.escalate("couldnt_run", "spec has no acceptance criteria, so nothing can be verified")
    return "build"
