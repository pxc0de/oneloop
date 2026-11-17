"""oneloop command line.

    oneloop run 123                 the whole loop for one issue (the main way to use it)
    oneloop run --label -j 3        work through labeled issues in every registered repo
    oneloop run --watch 10          ...and keep doing it every 10 minutes
    oneloop spec|build|...|land 123 one stage, for tuning prompts and debugging
    oneloop resume|approve|cancel 123, oneloop sendback 123 "note"
    oneloop status, oneloop logs 123 [stage], oneloop dashboard
    oneloop pause|unpause, oneloop automerge on|off
    oneloop init, oneloop learn, oneloop doctor
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

from oneloop import control, ledger, paths, pipeline, queue
from oneloop.github import GhCli, GitHubError, detect_repo
from oneloop.state import PHASES, RunState, all_runs, liveness, load, read_events

ACTIVE_ICON = {"active": "●", "stale": "⚠", "idle": " "}


def _repo_from_path(path: str) -> tuple[str, str, str]:
    """(owner/name, absolute checkout path, base branch)."""
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True)
    if top.returncode != 0:
        sys.exit(f"oneloop: {path} is not a git checkout")
    root = top.stdout.strip()
    repo, default = detect_repo(root)
    return repo, root, default


def _find_run(issue: int, repo_arg: str | None) -> RunState:
    if repo_arg and "/" in repo_arg and not os.path.exists(repo_arg):
        s = load(repo_arg, issue)
        if s:
            return s
        sys.exit(f"oneloop: no run for {repo_arg}#{issue}")
    candidates = [s for s in all_runs() if s.issue == issue]
    if repo_arg or len(candidates) != 1:
        repo, _, _ = _repo_from_path(repo_arg or ".")
        candidates = [s for s in candidates if s.repo == repo]
    if not candidates:
        sys.exit(f"oneloop: no run for issue #{issue}")
    return candidates[0]


def _report(s: RunState) -> int:
    if s.status == "merged":
        print(f"✓ {s.repo}#{s.issue} merged: {s.pr_url}  (${s.total_cost:.2f})")
        return 0
    if s.status == "needs_human":
        nh = s.needs_human
        print(f"⚑ {s.repo}#{s.issue} needs you ({nh.kind}): {nh.reason}")
        if nh.detail:
            print("\n" + nh.detail[:3000])
        print(f"\n  phase {s.phase} · ${s.total_cost:.2f} · {s.pr_url or 'no PR yet'}")
        return 2
    print(f"{s.repo}#{s.issue}: {s.status} at {s.phase}")
    return 0 if s.status != "failed" else 1


# --- commands ------------------------------------------------------------

def cmd_run(a: argparse.Namespace) -> int:
    if a.label or a.watch:
        repos = control.repos()
        if a.repo:
            repo, root, _ = _repo_from_path(a.repo)
            control.register_repo(repo, root)
            repos = {repo: root}
        elif not repos:
            repo, root, _ = _repo_from_path(".")
            control.register_repo(repo, root)
            repos = {repo: root}
        while True:
            n = queue.tick(repos, a.jobs)
            print(f"[{time.strftime('%H:%M:%S')}] started {n} run(s); {queue.active_count()} active")
            if not a.watch:
                return 0
            time.sleep(a.watch * 60)
    if not a.issues:
        sys.exit("oneloop run: give issue numbers, or --label to work through the queue")
    repo, root, default = _repo_from_path(a.repo or ".")
    gh = GhCli(repo)
    code = 0
    for issue in a.issues:
        try:
            s = pipeline.start(repo, root, issue, gh, default, require_trusted_label=a.queued)
        except pipeline.NotTrusted as e:
            print(f"oneloop: skipping {e}")
            continue
        if s.status == "needs_human":
            print(f"{repo}#{issue} is waiting on you; use `oneloop resume {issue}` once done.")
            code = max(code, _report(s))
            continue
        code = max(code, _report(pipeline.drive(s, gh)))
    return code


def cmd_stage(a: argparse.Namespace) -> int:
    repo, root, default = _repo_from_path(a.repo or ".")
    gh = GhCli(repo)
    s = pipeline.start(repo, root, a.issue, gh, default)
    if s.status == "needs_human":
        s.resume(touched=False)
    if s.status != "running":
        sys.exit(f"oneloop: run is {s.status}")
    return _report(pipeline.drive(s, gh, only=a.command))


def _action(a: argparse.Namespace, fn, *args) -> int:
    s = _find_run(a.issue, a.repo)
    gh = GhCli(s.repo)
    try:
        s = fn(s, gh, *args)
    except ValueError as e:
        sys.exit(f"oneloop: {e}")
    if getattr(a, "background", False):
        queue.spawn(["resume", str(s.issue), "--repo", s.repo], f"{paths.repo_key(s.repo)}-{s.issue}.log")
        print(f"{s.repo}#{s.issue}: continuing in the background")
        return 0
    return _report(pipeline.drive(s, gh))


def cmd_resume(a): return _action(a, pipeline.resume)
def cmd_approve(a): return _action(a, pipeline.approve)
def cmd_sendback(a): return _action(a, pipeline.sendback, a.note)


def cmd_cancel(a: argparse.Namespace) -> int:
    s = _find_run(a.issue, a.repo)
    pipeline.cancel(s, GhCli(s.repo), cleanup=not a.keep_worktree)
    print(f"{s.repo}#{s.issue} cancelled")
    return 0


def _ago(ts: float) -> str:
    d = max(0, time.time() - ts)
    return f"{int(d // 60)}m" if d < 3600 else (f"{d / 3600:.1f}h" if d < 86400 else f"{d / 86400:.1f}d")


def status_rows() -> list[dict]:
    rows = []
    for s in all_runs():
        live = {}
        try:
            live = json.loads((s.dir / "live.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        rows.append({
            "repo": s.repo, "issue": s.issue, "title": s.title, "status": s.status, "phase": s.phase,
            "liveness": liveness(s), "needs_human": s.needs_human.__dict__ if s.needs_human else None,
            "risk": (s.risk or {}).get("level"), "cost_usd": s.total_cost, "pr_url": s.pr_url,
            "rounds": s.rounds, "activity": live.get("activity", ""), "updated_at": s.updated_at,
            "created_at": s.created_at,
        })
    return rows


def cmd_status(a: argparse.Namespace) -> int:
    rows = status_rows()
    if not a.all:
        rows = [r for r in rows if r["status"] in ("running", "needs_human")]
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no active runs" + ("" if a.all else " (use --all for finished ones)"))
        return 0
    for r in sorted(rows, key=lambda r: (r["status"] != "needs_human", -r["updated_at"])):
        icon = "⚑" if r["status"] == "needs_human" else ACTIVE_ICON[r["liveness"]]
        what = (f"needs you: {r['needs_human']['kind']}: {r['needs_human']['reason']}"
                if r["needs_human"] else (r["activity"] or r["status"]))
        if r["liveness"] == "stale":
            what = "process died; `oneloop resume` to continue"
        print(f"{icon} {r['repo']}#{r['issue']:<5} {r['phase']:<7} ${r['cost_usd']:<6.2f} "
              f"{_ago(r['created_at']):>5}  {r['title'][:40]:<40}  {what[:80]}")
    if control.read()["paused"]:
        print("\n⏸  oneloop is paused: no new runs, no auto-merges")
    return 0


def cmd_logs(a: argparse.Namespace) -> int:
    s = _find_run(a.issue, a.repo)
    if a.stage:
        logs = sorted((s.dir / "sessions").glob(f"{a.stage}-*.jsonl"))
        if not logs:
            sys.exit(f"no session logs for {a.stage}")
        for line in logs[-1].read_text().splitlines():
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("type") == "assistant":
                for b in (m.get("message") or {}).get("content") or []:
                    if b.get("type") == "text":
                        print(b["text"])
                    elif b.get("type") == "tool_use":
                        print(f"  → {b.get('name')} {json.dumps(b.get('input'))[:200]}")
            elif m.get("type") == "result":
                print(f"[result {m.get('subtype')} ${m.get('total_cost_usd', 0):.2f} {m.get('num_turns')} turns]")
        return 0
    for e in read_events(s.dir):
        t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        rest = {k: v for k, v in e.items() if k not in ("ts", "type", "phase", "traceback")}
        print(f"{t} {e['phase']:<7} {e['type']:<16} {json.dumps(rest)[:160]}")
    return 0


def cmd_pause(a): control.set_paused(True); print("paused: no new runs, no auto-merges"); return 0
def cmd_unpause(a): control.set_paused(False); print("unpaused"); return 0


def _repo_name(arg: str | None) -> str:
    """owner/name from either 'owner/name' or a checkout path."""
    if arg and "/" in arg and not os.path.exists(arg):
        return arg
    return _repo_from_path(arg or ".")[0]


def cmd_automerge(a: argparse.Namespace) -> int:
    repo = _repo_name(a.repo)
    control.set_auto_merge(repo, a.state == "on")
    print(f"auto-merge {a.state} for {repo}")
    return 0


def cmd_dashboard(a: argparse.Namespace) -> int:
    from oneloop.dashboard.server import serve
    serve(a.port, open_browser=not a.no_open)
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    from oneloop import init_repo
    return init_repo.run(a.path, stress=a.stress)


def cmd_learn(a: argparse.Namespace) -> int:
    from oneloop import learn
    return learn.run(a.repo)


def cmd_doctor(a: argparse.Namespace) -> int:
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(f"{'✓' if good else '✗'} {text}")

    for tool in ("git", "gh", "claude"):
        line(shutil.which(tool) is not None, f"{tool} on PATH")
    p = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    line(p.returncode == 0, "gh is logged in")
    p = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    line(p.returncode == 0, f"claude {p.stdout.strip()}")
    line(sys.version_info >= (3, 11), f"python {sys.version.split()[0]}")
    print(f"  state: {paths.home()}  ({len(all_runs())} runs, {len(ledger.read())} ledger rows)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oneloop", description="Issue to merged PR, with a person only where needed.")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the whole loop")
    r.add_argument("issues", nargs="*", type=int)
    r.add_argument("--repo", help="checkout path (default: current directory)")
    r.add_argument("--label", action="store_true", help="work through issues with the trigger label")
    r.add_argument("-j", "--jobs", type=int, default=2, help="max runs at once in queue mode")
    r.add_argument("--watch", type=float, metavar="MIN", help="queue mode, repeated every MIN minutes")
    r.add_argument("--queued", action="store_true", help=argparse.SUPPRESS)
    r.set_defaults(fn=cmd_run)

    for phase in PHASES:
        p = sub.add_parser(phase, help=f"run only the {phase} stage")
        p.add_argument("issue", type=int)
        p.add_argument("--repo")
        p.set_defaults(fn=cmd_stage)

    for name, fn, helptext in (("resume", cmd_resume, "continue after you fixed what stopped it"),
                               ("approve", cmd_approve, "approve and merge a PR waiting on you")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("issue", type=int)
        p.add_argument("--repo", help="checkout path or owner/name")
        p.add_argument("--background", action="store_true")
        p.set_defaults(fn=fn)
    p = sub.add_parser("sendback", help="send back to the builder with a note")
    p.add_argument("issue", type=int)
    p.add_argument("note")
    p.add_argument("--repo")
    p.add_argument("--background", action="store_true")
    p.set_defaults(fn=cmd_sendback)
    p = sub.add_parser("cancel", help="stop a run")
    p.add_argument("issue", type=int)
    p.add_argument("--repo")
    p.add_argument("--keep-worktree", action="store_true")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("status", help="what is running and what needs you")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("logs", help="a run's events, or one stage's transcript")
    p.add_argument("issue", type=int)
    p.add_argument("stage", nargs="?", choices=PHASES)
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_logs)

    sub.add_parser("pause", help="no new runs, no auto-merges").set_defaults(fn=cmd_pause)
    sub.add_parser("unpause").set_defaults(fn=cmd_unpause)
    p = sub.add_parser("automerge", help="switch auto-merge for a repo")
    p.add_argument("state", choices=("on", "off"))
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_automerge)

    p = sub.add_parser("dashboard", help="local web dashboard")
    p.add_argument("--port", type=int, default=4747)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_dashboard)
    p = sub.add_parser("init", help="draft verify skill, REVIEW.md, checklist and config for a repo")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--stress", type=int, default=3, help="times to run the verify skill to check it is reliable")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("learn", help="propose checklist rules from what went wrong")
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_learn)
    sub.add_parser("doctor", help="check the tools oneloop needs").set_defaults(fn=cmd_doctor)

    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except GitHubError as e:
        print(f"oneloop: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; `oneloop resume` continues where it stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
