"""`oneloop learn`: turn what went wrong into proposed checklist rules, and
propose pruning rules that never earn their keep. Proposals go to a PR for a
person to approve; nothing changes the checklist on its own."""

from __future__ import annotations

import json
import subprocess
import time
from collections import Counter

from oneloop import checklist, claude, control, gitops, ledger, paths, rules, schemas
from oneloop.github import GhCli, detect_repo
from oneloop.prompts import render
from oneloop.state import all_runs, read_events


def gather(repo: str, repo_path: str, base: str) -> tuple[str, str]:
    """(signals, checklist activity) as text for the prompt."""
    signals: list[str] = []
    fired: Counter[str] = Counter()
    found: Counter[str] = Counter()
    for s in all_runs():
        if s.repo != repo:
            continue
        tag = f"#{s.issue}" + (f" (PR #{s.pr_number})" if s.pr_number else "")
        for e in read_events(s.dir):
            if e["type"] == "needs_human" and e.get("kind") not in ("approval",):
                signals.append(f"{tag} needed a person ({e['kind']}): {e.get('reason', '')}")
            elif e["type"] == "human.sendback":
                signals.append(f"{tag} sent back by a person: {e.get('note', '')}")
        for f in sorted((s.dir / "artifacts").glob("review-[0-9]*.json")):
            r = json.loads(f.read_text())
            for m in r.get("checklist_matched", []):
                fired[m["id"]] += 1
            for x in r.get("findings", []):
                if x.get("checklist_item"):
                    found[x["checklist_item"]] += 1
                if x["id"] in r.get("blocking", []):
                    signals.append(f"{tag} review blocked on `{x['file']}`: {x['summary']} "
                                   f"(scenario: {x['failure_scenario']})")
    for row in ledger.read():
        if row["repo"] == repo and row["outcome"] == "cancelled":
            signals.append(f"#{row['issue']} was cancelled by a person")
    reverts = gitops.git(repo_path, "log", f"origin/{base}", "--grep=^Revert", "-i", "--since=90 days ago",
                         "--format=%h %s", check=False)
    signals += [f"revert on {base}: {line}" for line in reverts.splitlines() if line.strip()]
    activity = "\n".join(f"- {k}: fired {fired[k]}x, led to {found[k]} finding(s)" for k in sorted(fired)) \
        or "(no checklist items have fired yet)"
    return "\n".join(f"- {x}" for x in signals) or "(none)", activity


def run(repo_arg: str | None) -> int:
    path = repo_arg or "."
    if repo_arg and "/" in repo_arg and repo_arg in control.repos():
        path = control.repos()[repo_arg]
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True,
                          text=True).stdout.strip()
    repo, base = detect_repo(root)
    gitops.fetch(root, base)
    current = gitops.show(root, f"origin/{base}", rules.CHECKLIST_PATH) or ""
    signals, activity = gather(repo, root, base)
    if signals == "(none)":
        print("nothing has gone wrong yet; no rules to learn")
        return 0
    work = paths.home() / "learn" / paths.repo_key(repo)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    res = claude.run(
        stage="learn", prompt=render("learn", repo=repo, checklist=current or "(empty)", activity=activity,
                                     signals=signals),
        cwd=root, model="opus", budget_usd=3.0, log_path=work / f"{stamp}.jsonl", schema=schemas.LEARN,
    )
    if not res.ok:
        print(f"learn session ended with {res.subtype}: {res.error}")
        return 1
    out = res.structured
    rules_md = "\n\n".join(
        f"## {r['id']}: {r['title']}\ntrigger: {', '.join(r['triggers'])}\ncheck: {r['check']}\norigin: {r['origin']}"
        for r in out["rules"])
    try:
        checklist.parse(rules_md)
    except checklist.ChecklistError as e:
        print(f"the proposed rules do not parse ({e}); see {work / (stamp + '.jsonl')}")
        return 1
    report = "\n\n".join([
        f"# oneloop learn: {repo} ({stamp})",
        "## Proposed rules\n\n" + (rules_md or "(none)"),
        "## Proposed pruning\n\n" + ("\n".join(f"- {p['id']}: {p['why']}" for p in out["prune"]) or "(none)"),
        f"## Notes\n\n{out['notes']}",
    ])
    (work / f"{stamp}.md").write_text(report)
    print(report)
    if not out["rules"] and not out["prune"]:
        return 0

    wt = work / f"wt-{stamp}"
    branch = f"oneloop/learn-{stamp}"
    gitops.add_worktree(root, wt, branch, f"origin/{base}")
    target = wt / rules.CHECKLIST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    text = target.read_text() if target.exists() else "# Reliability checklist\n"
    if rules_md:
        text = text.rstrip() + "\n\n" + rules_md + "\n"
    target.write_text(text)
    gitops.commit_all(wt, f"oneloop: propose checklist rules from recent misses\n\n{out['notes']}\n")
    gitops.push(wt, branch)
    gh = GhCli(repo)
    prune = "\n".join(f"- `{p['id']}`: {p['why']}" for p in out["prune"])
    gh.create_pr(branch, base, "oneloop: proposed checklist rules",
                 report + ("\n\nPruning is proposed only; remove those items by hand if you agree.\n" + prune
                           if prune else ""))
    pr = gh.pr_for_branch(branch)
    print(f"\nopened {pr.url if pr else 'a PR'}")
    gitops.remove_worktree(root, wt)
    return 0
