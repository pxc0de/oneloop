"""Numbers for the dashboard's metrics view, computed from the ledger, the
event logs and the review/verify artifacts. The first one is the goal:
human touches per merged change."""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from typing import Any

from oneloop import control, gitops, ledger
from oneloop.state import PHASES, all_runs, read_events

_revert_cache: dict[str, tuple[float, set[str]]] = {}


def _reverted_shas(repo: str) -> set[str]:
    path = control.repos().get(repo)
    if not path:
        return set()
    hit = _revert_cache.get(repo)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    out = gitops.git(path, "log", "--all", "--grep=This reverts commit", "--format=%b", check=False)
    shas = set()
    for line in out.splitlines():
        if "This reverts commit" in line:
            shas.add(line.split("This reverts commit", 1)[1].strip(" .").split()[0][:40])
    _revert_cache[repo] = (time.time(), shas)
    return shas


def compute(repo: str | None = None, days: int = 30) -> dict[str, Any]:
    since = time.time() - days * 86400
    rows = [r for r in ledger.read() if r["ts"] >= since and (repo is None or r["repo"] == repo)]
    merged = [r for r in rows if r["outcome"] == "merged"]
    cancelled = [r for r in rows if r["outcome"] == "cancelled"]

    def mean(xs: list[float]) -> float | None:
        return round(statistics.fmean(xs), 2) if xs else None

    def median(xs: list[float]) -> float | None:
        return round(statistics.median(xs), 1) if xs else None

    reverted = 0
    for r in merged:
        mc = r.get("merge_commit") or ""
        if mc and any(s.startswith(mc[:7]) or mc.startswith(s[:7]) for s in _reverted_shas(r["repo"])):
            reverted += 1

    # Cost per merged PR, stacked by stage (most recent 20).
    per_pr = [{"label": f"{r['repo'].split('/')[-1]}#{r['issue']}", "issue": r["issue"], "repo": r["repo"],
               "stages": {p: round(r["cost_by_stage"].get(p, 0.0), 3) for p in PHASES}, "total": r["cost_usd"]}
              for r in merged[-20:]]

    # Cost per day (all outcomes), last `days` days.
    by_day: dict[str, float] = defaultdict(float)
    for r in rows:
        by_day[time.strftime("%Y-%m-%d", time.localtime(r["ts"]))] += r["cost_usd"]
    day_list = []
    for i in range(min(days, 14) - 1, -1, -1):
        d = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        day_list.append({"day": d, "cost": round(by_day.get(d, 0.0), 2)})

    rounds = Counter()
    for r in merged:
        fixes = sum(r["rounds"].get(k, 0) for k in ("checks", "verify", "review", "ci"))
        rounds[min(fixes, 6)] += 1

    # Escalations, observe-mode gates and verify/validator health from the runs themselves.
    escalations: Counter[str] = Counter()
    observed: Counter[str] = Counter()
    verify_total = verify_cant = 0
    findings_total = findings_rejected = 0
    for s in all_runs():
        if repo and s.repo != repo or s.created_at < since:
            continue
        for e in read_events(s.dir):
            if e["type"] == "needs_human":
                escalations[e.get("kind", "?")] += 1
            elif e["type"] == "gate.observe":
                observed[e.get("gate", "?")] += 1
            elif e["type"] == "verify":
                verify_total += 1
                verify_cant += e.get("outcome") == "couldnt_run"
        for f in (s.dir / "artifacts").glob("review-[0-9]*.json"):
            try:
                rv = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            for x in rv.get("findings", []):
                if x.get("verdict"):
                    findings_total += 1
                    findings_rejected += x["verdict"] == "rejected"

    def rate(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    return {
        "repo": repo, "days": days,
        "merged": len(merged), "cancelled": len(cancelled),
        "human_touches_per_merged": mean([r["human_touches"] for r in merged]),
        "auto_merge_rate": rate(sum(r["auto_merged"] for r in merged), len(merged)),
        "median_issue_to_merge_s": median([r["elapsed_s"] for r in merged]),
        "median_human_wait_s": median([r["human_wait_s"] for r in merged]),
        "cost_per_merged": mean([r["cost_usd"] for r in merged]),
        "cost_total": round(sum(r["cost_usd"] for r in rows), 2),
        "revert_rate": rate(reverted, len(merged)),
        "rejected_rate": rate(len(cancelled), len(merged) + len(cancelled)),
        "validator_drop_rate": rate(findings_rejected, findings_total),
        "verify_couldnt_run_rate": rate(verify_cant, verify_total),
        "cost_per_pr": per_pr,
        "cost_per_day": day_list,
        "fix_rounds": [{"rounds": k, "count": rounds.get(k, 0)} for k in range(7)],
        "escalations": [{"kind": k, "count": v} for k, v in escalations.most_common()],
        "observed_gates": [{"gate": k, "count": v} for k, v in observed.most_common()],
        "repos": sorted({r["repo"] for r in ledger.read()} | set(control.repos())),
    }
