"""Thin git wrapper. Only the orchestrator commits, pushes and rebases; agent
sessions are denied those commands."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(cwd: str | Path, *args: str, check: bool = True, input: str | None = None) -> str:
    p = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, input=input
    )
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def ok(cwd: str | Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True).returncode == 0


def fetch(cwd: str | Path, ref: str) -> None:
    git(cwd, "fetch", "--quiet", "origin", ref)


def show(cwd: str | Path, ref: str, path: str) -> str | None:
    """A file's content at `ref`, or None if it does not exist there."""
    p = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=str(cwd), capture_output=True, text=True
    )
    return p.stdout if p.returncode == 0 else None


def ls_tree(cwd: str | Path, ref: str, path: str) -> list[str]:
    out = git(cwd, "ls-tree", "-r", "--name-only", ref, "--", path, check=False)
    return [line for line in out.splitlines() if line]


def head(cwd: str | Path) -> str:
    return git(cwd, "rev-parse", "HEAD").strip()


def rev(cwd: str | Path, ref: str) -> str:
    return git(cwd, "rev-parse", ref).strip()


def add_worktree(repo_path: str | Path, path: Path, branch: str, start: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(repo_path, "worktree", "add", "-B", branch, str(path), start)


def remove_worktree(repo_path: str | Path, path: Path) -> None:
    git(repo_path, "worktree", "remove", "--force", str(path), check=False)
    git(repo_path, "worktree", "prune", check=False)


def status_porcelain(cwd: str | Path) -> str:
    return git(cwd, "status", "--porcelain", "--untracked-files=all")


def discard_changes(cwd: str | Path) -> None:
    git(cwd, "reset", "--hard", "--quiet")
    git(cwd, "clean", "-fdq")


def commit_all(cwd: str | Path, message: str) -> bool:
    """Commit everything in the worktree with the user's own git identity.
    Returns False when there was nothing to commit."""
    git(cwd, "add", "-A")
    if ok(cwd, "diff", "--cached", "--quiet"):
        return False
    git(cwd, "commit", "--quiet", "--no-verify", "-F", "-", input=message)
    return True


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    deleted: int
    status: str  # A, M, D, R...


def changes(cwd: str | Path, base: str, head_ref: str = "HEAD") -> list[FileChange]:
    numstat = git(cwd, "diff", "--numstat", "-M", f"{base}...{head_ref}")
    names = git(cwd, "diff", "--name-status", "-M", f"{base}...{head_ref}")
    status_by_path: dict[str, str] = {}
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status_by_path[parts[-1]] = parts[0][0]
    out = []
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, path = parts[0], parts[1], parts[-1]
        if " => " in path:  # rename shorthand like src/{a => b}.py
            path = _rename_target(path)
        out.append(
            FileChange(
                path=path,
                added=int(a) if a.isdigit() else 0,
                deleted=int(d) if d.isdigit() else 0,
                status=status_by_path.get(path, "M"),
            )
        )
    return out


def _rename_target(path: str) -> str:
    if "{" in path:
        pre, rest = path.split("{", 1)
        mid, post = rest.split("}", 1)
        return pre + mid.split(" => ")[1] + post
    return path.split(" => ")[1]


def diff(cwd: str | Path, base: str, head_ref: str = "HEAD", triple_dot: bool = True) -> str:
    spec = f"{base}...{head_ref}" if triple_dot else f"{base}..{head_ref}"
    return git(cwd, "diff", "-M", spec)


def added_lines(diff_text: str) -> list[tuple[str, str]]:
    """(path, line) for every added line in a unified diff."""
    out = []
    path = ""
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and not line.startswith("+++") and path:
            out.append((path, line[1:]))
    return out


def patch_id(cwd: str | Path, base: str, head_ref: str = "HEAD") -> str:
    """Stable id of the change's content. Survives a clean rebase, so a review
    of the same diff carries over while any real change invalidates it."""
    d = diff(cwd, base, head_ref)
    if not d.strip():
        return ""
    out = git(cwd, "patch-id", "--stable", input=d)
    return out.split()[0] if out.strip() else ""


def push(cwd: str | Path, branch: str, force_with_lease: bool = False) -> None:
    args = ["push", "--quiet", "-u", "origin", f"HEAD:refs/heads/{branch}"]
    if force_with_lease:
        args.insert(1, "--force-with-lease")
    git(cwd, *args)


def remote_sha(cwd: str | Path, branch: str) -> str | None:
    out = git(cwd, "ls-remote", "origin", f"refs/heads/{branch}", check=False)
    return out.split()[0] if out.strip() else None


def rebase(cwd: str | Path, onto: str) -> bool:
    """Rebase onto `onto`. On conflict, abort and return False."""
    if ok(cwd, "rebase", "--quiet", onto):
        return True
    git(cwd, "rebase", "--abort", check=False)
    return False


def is_ancestor(cwd: str | Path, ancestor: str, descendant: str) -> bool:
    return ok(cwd, "merge-base", "--is-ancestor", ancestor, descendant)


def merge_start(cwd: str | Path, onto: str) -> list[str]:
    """Start merging `onto` without committing. Returns the conflicted paths
    (empty if it merged cleanly, in which case the merge is still uncommitted)."""
    git(cwd, "merge", "--no-commit", "--no-ff", onto, check=False)
    out = git(cwd, "diff", "--name-only", "--diff-filter=U")
    return [p for p in out.splitlines() if p]


def merging(cwd: str | Path) -> bool:
    return ok(cwd, "rev-parse", "-q", "--verify", "MERGE_HEAD")


def conflict_markers(cwd: str | Path) -> list[str]:
    """Tracked files that still contain conflict markers."""
    out = git(cwd, "grep", "-l", "-E", "^(<<<<<<<|>>>>>>>)( |$)", check=False)
    return [p for p in out.splitlines() if p]
