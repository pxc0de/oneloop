"""Markdown views of run artifacts: for prompts, the PR body, the brief and
the merge-commit stamp."""

from __future__ import annotations

from typing import Any

ICON = {"pass": "✅", "fail": "❌", "couldnt_run": "⚠️", "skipped": "➖"}


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def spec_md(spec: dict[str, Any]) -> str:
    ts = spec.get("test_strategy") or {}
    parts = [
        f"**Summary.** {spec.get('summary', '')}",
        "**Acceptance criteria**\n" + criteria_md(spec.get("acceptance_criteria") or []),
        "**Assumptions**\n" + bullets(spec.get("assumptions") or []),
        "**Plan**\n" + "\n".join(f"{i}. {p}" for i, p in enumerate(spec.get("plan") or [], 1)),
        "**Test strategy**\n" + bullets([f"{k}: {v}" for k, v in ts.items() if v]),
        "**Out of scope**\n" + bullets(spec.get("out_of_scope") or []),
    ]
    return "\n\n".join(parts)


def criteria_md(criteria: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- **{c['id']}**: {c['description']}\n  - How to verify: {c['how_to_verify']}" for c in criteria
    ) or "- (none)"


def verify_table(verify: dict[str, Any] | None) -> str:
    if not verify:
        return "_Not verified._"
    rows = ["| | Criterion | Observed |", "|---|---|---|"]
    for c in verify.get("criteria", []):
        observed = (c.get("observed") or "").replace("\n", " ").replace("|", "\\|")[:300]
        rows.append(f"| {ICON.get(c['outcome'], '?')} | **{c['id']}** {c.get('description', '')} | {observed} |")
    return "\n".join(rows)


def findings_md(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "- (none)"
    return "\n".join(
        f"- **{f['id']}** [{f.get('severity')}] `{f.get('file')}:{f.get('line')}`: {f.get('summary')}"
        + (f"\n  - Failure scenario: {f['failure_scenario']}" if f.get("failure_scenario") else "")
        + (f"\n  - Validator: {f['verdict']}: {f.get('verdict_reason', '')}" if f.get("verdict") else "")
        for f in findings
    )


def risk_md(risk: dict[str, Any] | None) -> str:
    if not risk:
        return "_Not assessed._"
    lines = [f"**{risk['level']}** ({risk.get('lines', 0)} lines, {risk.get('files', 0)} files)"]
    for r in risk.get("reasons", []):
        tag = " _(observe mode: not applied)_" if r.get("observed_only") else ""
        lines.append(f"- {r['level']}: {r['detail']}{tag}")
    return "\n".join(lines)


def stamp(state: Any, verify: dict[str, Any] | None, review: dict[str, Any] | None,
          checks: list[dict[str, Any]] | None) -> str:
    """One line recording how the merged change was verified, queryable from git log."""
    sha = (state.head_sha or "")[:7]
    if verify:
        crit = verify.get("criteria", [])
        passed = sum(1 for c in crit if c["outcome"] == "pass")
        v = f"e2e {passed}/{len(crit)}"
    else:
        v = "none"
    ran = [c["name"] for c in (checks or []) if c["outcome"] == "pass"]
    r = f"r{review['round']} clean" if review else "none"
    risk = (state.risk or {}).get("level", "unknown")
    approver = "human" if state.approved_sha else "auto"
    return (f"Oneloop-Verified: verify {v}; checks {','.join(ran) or 'none'}; review {r}; "
            f"risk {risk}; merge {approver} @{sha}")


def pr_body(state: Any, spec: dict[str, Any], build: dict[str, Any] | None,
            verify: dict[str, Any] | None, review: dict[str, Any] | None,
            checks: list[dict[str, Any]] | None) -> str:
    parts = [
        f"Closes #{state.issue}",
        f"## Summary\n\n{spec.get('summary', '')}",
    ]
    if build and build.get("summary"):
        parts.append(f"## What changed\n\n{build['summary']}")
    parts.append("## Verification\n\n" + verify_table(verify))
    if checks:
        parts.append("**Checks**: " + ", ".join(
            f"{ICON.get(c['outcome'], '?')} `{c['command']}`" for c in checks if c["outcome"] != "skipped"
        ))
    if review:
        nits = [f for f in review.get("findings", []) if f.get("severity") == "nit" and f.get("verdict") != "rejected"]
        pre = [f for f in review.get("findings", []) if f.get("severity") == "pre_existing" and f.get("verdict") != "rejected"]
        rv = [f"Round {review['round']}: no open important findings. {review.get('summary', '')}"]
        if nits:
            rv.append("**Nits (not blocking)**\n" + findings_md(nits))
        if pre:
            rv.append("**Pre-existing issues (not introduced here)**\n" + findings_md(pre))
        parts.append("## Review\n\n" + "\n\n".join(rv))
    parts.append("## Risk\n\n" + risk_md(state.risk))
    if spec.get("assumptions"):
        parts.append("## Assumptions\n\n" + bullets(spec["assumptions"]))
    parts.append(f"---\n<sub>{stamp(state, verify, review, checks)}</sub>")
    return "\n\n".join(parts)


def brief_md(brief: dict[str, Any], why_human: str, issue: int) -> str:
    look = "\n".join(f"- `{x['file']}` {x['lines']}: {x['why']}" for x in brief.get("look_at") or [])
    return "\n\n".join([
        f"### oneloop brief: {brief.get('headline', '')}",
        f"**Why you're seeing this:** {why_human}",
        "**What changed**\n" + bullets(brief.get("what_changed") or []),
        "**Evidence**\n" + bullets(brief.get("evidence") or []),
        "**Risks**\n" + bullets(brief.get("risks") or []),
        "**Look at**\n" + (look or "- (nothing specific)"),
        f"**Recommendation:** {brief.get('recommendation', '?').replace('_', ' ')}. "
        f"{brief.get('recommendation_reason', '')}",
        f"Approve: `oneloop approve {issue}`, key 1 in `/oneloop`, or the dashboard. "
        f"Send back: `oneloop sendback {issue} \"what to change\"`.",
    ])
