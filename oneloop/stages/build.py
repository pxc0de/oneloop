"""BUILD: one session that implements the spec and is resumed for every fix
round, because its own context is the best thing to fix with. Only the
orchestrator commits; the builder never touches history."""

from __future__ import annotations

import json
import sys
from typing import Any

from oneloop import gitops, paths, render, rules, schemas
from oneloop.context import RunContext
from oneloop.prompts import UNTRUSTED_NOTE, render as render_prompt, untrusted

SOURCE_NAMES = {
    "checks": "the repo's checks",
    "verify": "an independent verifier who ran the change",
    "review": "an independent code review (findings confirmed by a second session)",
    "ci": "CI on the pull request",
    "human": "a person",
    "rebase": "merging the latest base branch, which changed the same code",
}


def _check_commands(ctx: RunContext) -> str:
    cmds = [c for c in ctx.cfg.check_commands().values() if c]
    return ", ".join(f"`{c}`" for c in cmds) or "(none configured; run the project's tests the usual way)"


def _stop_hook(ctx: RunContext, round_: int) -> tuple[dict[str, Any], dict[str, str]]:
    script = paths.package_dir() / "hooks" / "stop_checks.py"
    settings = {"hooks": {"Stop": [{"hooks": [{
        "type": "command",
        "command": f'"{sys.executable}" -I "{script}"',
        "timeout": int(ctx.cfg["checks"]["timeout_s"]) * 2,
    }]}]}}
    cmds = ctx.cfg.check_commands()
    env = {
        "ONELOOP_STOP_CHECKS": json.dumps({k: cmds[k] for k in ctx.cfg["checks"]["stop_hook"] if cmds.get(k)}),
        "ONELOOP_STOP_STATE": str(ctx.dir / f"stop-hook-{round_}.count"),
        "ONELOOP_STOP_TIMEOUT": str(ctx.cfg["checks"]["timeout_s"]),
    }
    return settings, env


def _items_md(items: list[dict[str, Any]]) -> str:
    out = []
    for it in items:
        out.append(f"### [{it['id']}] from {it['source']}: {it['summary']}")
        if it.get("detail"):
            out.append(it["detail"].strip())
    return "\n\n".join(out)


def run(ctx: RunContext) -> str:
    s = ctx.state
    s.rounds["build"] += 1
    rnd = s.rounds["build"]
    with ctx.stage("build", rnd) as rec:
        ctx.refresh_rules()
        spec = ctx.read_artifact("spec.json")
        issue = ctx.gh.issue(s.issue)
        settings, env = _stop_hook(ctx, rnd)
        common = dict(
            schema=schemas.BUILD, settings=settings, env=env,
            extra_allowed=list(ctx.cfg["agent"]["allowed"]),
        )
        protected = ", ".join(f"`{p}`" for p in rules.PROTECTED)
        fresh_prompt = render_prompt(
            "build", guidance=ctx.rules.guidance_for("build"), untrusted_note=UNTRUSTED_NOTE,
            branch=s.branch, issue=str(s.issue), title=s.title,
            issue_body=untrusted(issue.body or "(empty)"), spec=render.spec_md(spec),
            check_commands=_check_commands(ctx), protected=protected,
        )
        feedback = list(s.feedback)
        if feedback:
            sources = sorted({f["source"] for f in feedback})
            rethink = ""
            if s.rounds["review"] >= ctx.cfg["caps"]["review_rounds"] and "review" in sources:
                rethink = (
                    "**This change has now failed review "
                    f"{s.rounds['review']} times.** Before patching again, step back. Re-read the spec "
                    "and the findings together and ask whether the approach itself is wrong. If a "
                    "different design would make these findings disappear, switch to it. This is the "
                    "last round before a person is brought in."
                )
            fix_prompt = render_prompt(
                "fix", guidance=ctx.rules.guidance_for("build"), round=str(rnd),
                sources=", ".join(SOURCE_NAMES.get(x, x) for x in sources),
                check_commands=_check_commands(ctx), rethink=rethink,
                items=_items_md(feedback), protected=protected,
            )
            res = None
            if s.build_session:
                res = ctx.session("build", fix_prompt, budget_key="fix", resume=s.build_session, **common)
            if res is None or res.subtype == "no_result":
                # The old session is gone: start fresh with the spec and the work so far.
                res = ctx.session(
                    "build",
                    fresh_prompt + "\n\n## Work so far\n\nThe branch already contains an earlier attempt "
                    "(see `git log` and `git diff " + ctx.rules.base_ref + "...HEAD`). Continue from it.\n\n"
                    + fix_prompt,
                    budget_key="build", **common,
                )
        else:
            res = ctx.session("build", fresh_prompt, **common)

        s.build_session = res.session_id
        if not res.ok:
            ctx.escalate("couldnt_run", f"build session ended with {res.subtype}", res.error)
        out = res.structured
        ctx.write_artifact(f"build-{rnd}.json", out)
        if out["blocked"]:
            ctx.escalate("builder_blocked", out["blocker"] or "the builder says it is blocked")

        expected = s.head_sha or s.start_sha
        actual = gitops.head(ctx.wt)
        if expected and actual != expected:
            ctx.escalate("changed_under_us",
                         f"git history changed during the build session (HEAD {actual[:7]}, expected {expected[:7]})",
                         "Agents may not commit or rewrite history. Inspect the worktree: " + str(ctx.wt))

        left = gitops.conflict_markers(ctx.wt)
        if left:
            ctx.escalate("conflict", "conflict markers remain after the builder's round",
                         "\n".join(f"- {f}" for f in left) + f"\n\nResolve in {ctx.wt} and resume.")
        message = (
            f"oneloop: {'merge ' + ctx.rules.base_ref + ' and ' if gitops.merging(ctx.wt) else ''}"
            f"{'build' if not feedback else 'fix'} round {rnd} for #{s.issue}\n\n"
            f"{out['summary'].strip()}\n"
        )
        committed = gitops.commit_all(ctx.wt, message)
        if not committed:
            if not feedback:
                ctx.escalate("builder_blocked", "the builder finished without changing any files")
            declined = out.get("not_addressed") or []
            ctx.escalate("builder_blocked",
                         "the builder made no changes in a fix round",
                         "\n".join(f"- {d['id']}: {d['why']}" for d in declined)
                         or "It addressed nothing and gave no reason.")
        s.head_sha = gitops.head(ctx.wt)
        s.feedback = []
        if out.get("not_addressed"):
            ctx.emit("feedback.declined", items=out["not_addressed"])
        rec.note = out["summary"][:300]
    return "checks"
