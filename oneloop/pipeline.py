"""The loop: a state machine over the stages, plus the actions a person takes
(approve, send back, resume, cancel). The full loop and single-stage runs use
the same code path, so they cannot drift apart."""

from __future__ import annotations

import re
import traceback
from pathlib import Path

from oneloop import control, gitops, ledger, paths
from oneloop.context import RunContext, Stop
from oneloop.effects import EffectUncertain, perform
from oneloop.github import GitHub, GitHubError
from oneloop.rules import load as load_rules
from oneloop.stages import RUNNERS
from oneloop.state import RunLock, RunState, emit, load, save


class NotTrusted(RuntimeError):
    pass


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "change"


def start(repo: str, repo_path: str, issue: int, gh: GitHub, base: str,
          require_trusted_label: bool = False, fetch: bool = True) -> RunState:
    """Create (or load) the run, its worktree and its claim on the issue."""
    s = load(repo, issue)
    if s is not None:
        return s
    iss = gh.issue(issue)
    if require_trusted_label:
        trigger = load_rules(repo_path, base, fetch=fetch).config["labels"]["trigger"]
        who = gh.labeled_by(issue, trigger)
        if not who or gh.permission(who) not in ("admin", "maintain", "write"):
            raise NotTrusted(f"#{issue}: label '{trigger}' was not applied by someone with write access")
    s = RunState(repo=repo, issue=issue, repo_path=repo_path, title=iss.title, base=base)
    s.branch = f"oneloop/{issue}-{slug(iss.title)}"
    wt = paths.worktree_dir(repo, issue)
    s.worktree = str(wt)
    if fetch:
        gitops.fetch(repo_path, base)
    s.start_sha = gitops.rev(repo_path, f"origin/{base}")
    if not wt.exists():
        gitops.add_worktree(repo_path, wt, s.branch, s.start_sha)
    save(s)
    emit(s, "run.start", title=iss.title, branch=s.branch, base=base)
    control.register_repo(repo, repo_path)

    gh.ensure_labels()
    perform(s, f"claim:{issue}",
            act=lambda: (gh.add_labels(issue, ["oneloop:claimed"]), gh.assign_self(issue)),
            confirm=lambda: True if "oneloop:claimed" in gh.issue(issue).labels else None)
    return s


def drive(s: RunState, gh: GitHub, only: str | None = None, fetch: bool = True) -> RunState:
    """Run stages until the loop stops. With `only`, run just that stage."""
    with RunLock(s.dir):
        if s.status != "running":
            return s
        try:
            ctx = RunContext(s, gh, fetch=fetch)
            while s.status == "running":
                if only and s.phase != only:
                    s.to_phase(only)
                nxt = RUNNERS[s.phase](ctx)
                if nxt == "done" or s.status != "running":
                    break
                s.to_phase(nxt)
                save(s)
                if only:
                    break
        except Stop:
            pass
        except EffectUncertain as e:
            s.ask_human("uncertain_effect", str(e),
                        "Check GitHub, then resume; oneloop confirms before acting again.")
            emit(s, "needs_human", kind="uncertain_effect", reason=str(e))
        except Exception as e:  # unattended: any bug becomes a visible, resumable stop
            s.ask_human("error", f"{type(e).__name__}: {e}"[:500])
            emit(s, "error", error=str(e), traceback=traceback.format_exc()[-4000:])
        finally:
            save(s)
    return s


# --- actions a person takes ------------------------------------------------

def _clear_needs_human_label(s: RunState, gh: GitHub) -> None:
    try:
        gh.remove_label(s.issue, "oneloop:needs-human")
    except GitHubError:
        pass


def approve(s: RunState, gh: GitHub) -> RunState:
    if s.status != "needs_human" or s.needs_human.kind != "approval":
        raise ValueError(f"#{s.issue} is not waiting for approval")
    s.approved_sha = s.head_sha
    s.resume()
    s.to_phase("land")
    emit(s, "human.approve", sha=s.head_sha)
    save(s)
    _clear_needs_human_label(s, gh)
    return s


def sendback(s: RunState, gh: GitHub, note: str) -> RunState:
    if s.status not in ("needs_human", "running"):
        raise ValueError(f"#{s.issue} is {s.status}")
    s.feedback.append({"id": f"H{s.human_touches + 1}", "source": "human", "summary": note, "detail": ""})
    if s.status == "needs_human":
        s.resume()
    else:
        s.human_touches += 1
    s.approved_sha = None
    s.to_phase("build")
    emit(s, "human.sendback", note=note)
    save(s)
    _clear_needs_human_label(s, gh)
    return s


def resume(s: RunState, gh: GitHub) -> RunState:
    """Pick up after a person fixed what stopped the run."""
    if s.status == "running":
        return s
    if s.status != "needs_human":
        raise ValueError(f"#{s.issue} is {s.status}")
    kind = s.needs_human.kind
    s.resume()
    if kind == "spec_questions":
        s.to_phase("spec")
    elif kind == "cap_reached":
        for k in ("checks", "verify", "review", "ci"):
            s.rounds[k] = 0  # a person chose to give it more rounds
    emit(s, "human.resume", kind=kind)
    save(s)
    _clear_needs_human_label(s, gh)
    return s


def cancel(s: RunState, gh: GitHub, cleanup: bool = True) -> RunState:
    s.finish("cancelled")
    emit(s, "human.cancel")
    ledger.record(s, "cancelled")
    save(s)
    for label in ("oneloop:claimed", "oneloop:needs-human"):
        try:
            gh.remove_label(s.issue, label)
        except GitHubError:
            pass
    if cleanup and Path(s.worktree).exists():
        gitops.remove_worktree(s.repo_path, Path(s.worktree))
    return s
