"""`oneloop init`: draft the rules that will judge this repo's changes, then
prove the verify skill is reliable by running it several times, and open a
PR for a person to review. These files are protected, so they never merge
without a person."""

from __future__ import annotations

import subprocess
import time

from oneloop import checks, claude, config as config_mod, gitops, paths, rules, schemas
from oneloop.github import GhCli, detect_repo
from oneloop.prompts import render

INIT_FILES = (rules.CONFIG_PATH, f"{rules.VERIFY_SKILL_DIR}/SKILL.md", rules.REVIEW_PATH, rules.CHECKLIST_PATH)


def _log(msg: str) -> None:
    print(f"[init] {msg}", flush=True)


def run(path: str, stress: int = 3) -> int:
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True,
                          text=True).stdout.strip()
    if not root:
        raise SystemExit(f"oneloop init: {path} is not a git checkout")
    repo, base = detect_repo(root)
    gh = GhCli(repo)
    gitops.fetch(root, base)
    existing = [f for f in INIT_FILES if gitops.show(root, f"origin/{base}", f) is not None]
    if len(existing) == len(INIT_FILES):
        _log("every oneloop file already exists on the base branch; nothing to draft")
        return 0

    work = paths.home() / "init" / paths.repo_key(repo) / time.strftime("%Y%m%d-%H%M%S")
    wt = work / "worktree"
    branch = "oneloop/init"
    gitops.add_worktree(root, wt, branch, f"origin/{base}")
    _log(f"drafting in {wt}")
    res = claude.run(
        stage="init", prompt=render("init"), cwd=wt, model="opus", budget_usd=10.0,
        log_path=work / "init.jsonl", schema=schemas.INIT,
        on_activity=_log,
    )
    if not res.ok:
        _log(f"init session ended with {res.subtype}: {res.error}")
        return 1
    out = res.structured
    _log(f"wrote {', '.join(out['files_written']) or 'nothing'} (${res.cost_usd:.2f})")

    # The orchestrator re-runs the checks itself rather than trusting the session.
    cfg_text = (wt / rules.CONFIG_PATH).read_text() if (wt / rules.CONFIG_PATH).exists() else None
    try:
        cfg = config_mod.load(cfg_text, source="draft")
    except config_mod.ConfigError as e:
        _log(f"the drafted .oneloop.toml is invalid: {e}")
        return 1
    check_results = checks.run_all(cfg.check_commands(), wt, int(cfg["checks"]["timeout_s"]))
    for r in check_results:
        _log(f"check {r.name}: {r.outcome} `{r.command}`")

    skill = wt / rules.VERIFY_SKILL_DIR / "SKILL.md"
    smoke = []
    if skill.exists() and stress > 0:
        for i in range(1, stress + 1):
            ev = work / f"smoke-{i}"
            ev.mkdir(parents=True, exist_ok=True)
            before = gitops.status_porcelain(wt)
            started = time.time()
            r = claude.run(
                stage="verify", prompt=render("smoke", evidence_dir=str(ev), verify_skill=skill.read_text()),
                cwd=wt, model="sonnet", budget_usd=3.0, log_path=work / f"smoke-{i}.jsonl",
                schema=schemas.SMOKE, add_dirs=[str(ev)],
            )
            tampered = gitops.status_porcelain(wt) != before
            outcome = "couldnt_run" if (not r.ok or tampered) else r.structured["outcome"]
            observed = "modified the worktree" if tampered else ((r.structured or {}).get("observed") or r.error)
            smoke.append({"run": i, "outcome": outcome, "observed": observed[:300],
                          "seconds": round(time.time() - started), "cost": r.cost_usd})
            _log(f"smoke {i}/{stress}: {outcome} in {smoke[-1]['seconds']}s")
    reliable = bool(smoke) and all(s["outcome"] == "pass" for s in smoke)

    if not gitops.commit_all(wt, "oneloop: set up verification and review rules\n"):
        _log("nothing to commit")
        return 1
    gitops.push(wt, branch, force_with_lease=True)
    body = "\n\n".join([
        "Drafted by `oneloop init`. These files judge every change oneloop makes, and they are read from "
        f"`{base}` only, so nothing applies until you merge this. Edit them freely: the people who own the "
        "code should own its checks.",
        "## Files\n" + "\n".join(f"- `{f}`" for f in out["files_written"]),
        "## Checks (re-run by oneloop)\n" + "\n".join(
            f"- {checks_icon(r.outcome)} {r.name}: `{r.command}`" for r in check_results if r.outcome != "skipped"),
        "## Verify skill stress test\n" + (
            ("Reliable: " if reliable else "**Not reliable yet**: ") + f"{sum(s['outcome'] == 'pass' for s in smoke)}"
            f"/{len(smoke)} runs passed.\n\n" + "\n".join(
                f"- run {s['run']}: {s['outcome']} in {s['seconds']}s: {s['observed']}" for s in smoke)
            if smoke else "Not run."),
        f"## How it verifies\n{out['verify_summary']}",
        f"## Notes\n{out['notes']}" if out["notes"] else "",
    ])
    gh.create_pr(branch, base, "oneloop: set up verification and review rules", body)
    pr = gh.pr_for_branch(branch)
    _log(f"opened {pr.url if pr else '(PR not visible yet)'}")
    gitops.remove_worktree(root, wt)
    return 0 if reliable or not smoke else 3


def checks_icon(outcome: str) -> str:
    return {"pass": "✅", "fail": "❌", "couldnt_run": "⚠️"}.get(outcome, "➖")

