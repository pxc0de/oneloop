"""One line per finished run, across all repos. The metrics view, `learn`
and any future reviewer eval read from here."""

from __future__ import annotations

import json
from typing import Any

from oneloop import paths
from oneloop.state import RunState, now


def record(state: RunState, outcome: str, **extra: Any) -> None:
    durations: dict[str, float] = {}
    for st in state.stages:
        if st.ended:
            durations[st.phase] = round(durations.get(st.phase, 0.0) + (st.ended - st.started), 1)
    row = {
        "ts": now(),
        "repo": state.repo,
        "issue": state.issue,
        "title": state.title,
        "pr": state.pr_number,
        "outcome": outcome,
        "risk": (state.risk or {}).get("level"),
        "auto_merged": outcome == "merged" and not state.approved_sha,
        "human_touches": state.human_touches,
        "cost_usd": state.total_cost,
        "cost_by_stage": state.costs,
        "duration_by_stage": durations,
        "elapsed_s": round(now() - state.created_at, 1),
        "human_wait_s": round(state.human_wait_s, 1),
        "ci_wait_s": round(state.ci_wait_s, 1),
        "rounds": state.rounds,
        **extra,
    }
    p = paths.ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(row) + "\n")


def read() -> list[dict[str, Any]]:
    try:
        lines = paths.ledger_path().read_text().splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
