"""GitHub access through the `gh` CLI. The orchestrator is the only thing that
writes to GitHub; agent sessions never get `gh`.

`GitHub` is an interface so tests can swap in an in-memory fake.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

MARKER = "<!-- oneloop:{key} -->"

LABELS = {
    "oneloop:claimed": ("5319e7", "oneloop is working on this"),
    "oneloop:pr": ("1d76db", "oneloop opened a PR for this"),
    "oneloop:needs-human": ("d93f0b", "oneloop needs a person to act"),
    "oneloop:hold": ("b60205", "do not merge: oneloop will not auto-merge while this is set"),
}

WRITE_PERMISSIONS = ("admin", "maintain", "write")


class GitHubError(RuntimeError):
    pass


@dataclass
class Comment:
    id: int
    author: str
    body: str
    created_at: str
    is_bot: bool = False


@dataclass
class Issue:
    number: int
    title: str
    body: str
    state: str
    labels: list[str]
    author: str
    url: str
    comments: list[Comment] = field(default_factory=list)


@dataclass
class PullRequest:
    number: int
    url: str
    state: str  # OPEN, MERGED, CLOSED
    head_sha: str
    head_branch: str
    base: str
    body: str = ""
    merge_state: str = ""  # CLEAN, BEHIND, DIRTY, BLOCKED, UNSTABLE, UNKNOWN
    review_decision: str = ""  # APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, ""
    labels: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    merge_commit: str | None = None


@dataclass
class Check:
    name: str
    bucket: str  # pass, fail, pending, skipping, cancel
    link: str = ""


class GitHub:
    """The operations the loop needs. Implemented by GhCli and tests' FakeGitHub."""

    repo: str

    def whoami(self) -> str: raise NotImplementedError
    def issue(self, n: int) -> Issue: raise NotImplementedError
    def list_issues(self, label: str) -> list[Issue]: raise NotImplementedError
    def labeled_by(self, n: int, label: str) -> str | None: raise NotImplementedError
    def permission(self, login: str) -> str: raise NotImplementedError
    def ensure_labels(self) -> None: raise NotImplementedError
    def add_labels(self, n: int, labels: list[str]) -> None: raise NotImplementedError
    def remove_label(self, n: int, label: str) -> None: raise NotImplementedError
    def assign_self(self, n: int) -> None: raise NotImplementedError
    def comments(self, n: int) -> list[Comment]: raise NotImplementedError
    def create_comment(self, n: int, body: str) -> None: raise NotImplementedError
    def edit_comment(self, comment_id: int, body: str) -> None: raise NotImplementedError
    def pr_for_branch(self, branch: str) -> PullRequest | None: raise NotImplementedError
    def pr(self, n: int) -> PullRequest: raise NotImplementedError
    def create_pr(self, branch: str, base: str, title: str, body: str) -> None: raise NotImplementedError
    def edit_pr_body(self, n: int, body: str) -> None: raise NotImplementedError
    def checks(self, n: int) -> list[Check]: raise NotImplementedError
    def failed_log(self, check: Check) -> str: raise NotImplementedError
    def merge(self, n: int, sha: str, method: str, subject: str, body: str) -> None: raise NotImplementedError

    # --- helpers built on the primitives ---------------------------------
    def find_comment(self, n: int, key: str) -> Comment | None:
        marker = MARKER.format(key=key)
        for c in self.comments(n):
            if marker in c.body:
                return c
        return None

    def upsert_comment_body(self, n: int, key: str, body: str) -> tuple[str, int | None]:
        """Returns (marked body, existing comment id or None)."""
        marked = f"{MARKER.format(key=key)}\n{body}"
        existing = self.find_comment(n, key)
        return marked, existing.id if existing else None


def _gh_bin() -> str:
    return os.environ.get("ONELOOP_GH_BIN", "gh")


class GhCli(GitHub):
    def __init__(self, repo: str, cwd: str | None = None):
        self.repo = repo
        self.cwd = cwd
        self._me: str | None = None

    def _run(self, *args: str, input: str | None = None, check: bool = True) -> str:
        p = subprocess.run(
            [_gh_bin(), *args], cwd=self.cwd, capture_output=True, text=True, input=input
        )
        if check and p.returncode != 0:
            raise GitHubError(f"gh {' '.join(args[:3])}: {p.stderr.strip() or p.stdout.strip()}")
        return p.stdout

    def _json(self, *args: str) -> Any:
        out = self._run(*args)
        return json.loads(out) if out.strip() else None

    def _api(self, path: str, *args: str) -> Any:
        return self._json("api", path.format(repo=self.repo), *args)

    # identity
    def whoami(self) -> str:
        if not self._me:
            self._me = self._run("api", "user", "-q", ".login").strip()
        return self._me

    # issues
    def issue(self, n: int) -> Issue:
        d = self._json(
            "issue", "view", str(n), "-R", self.repo,
            "--json", "number,title,body,state,labels,author,url",
        )
        return Issue(
            number=d["number"], title=d["title"], body=d.get("body") or "",
            state=d["state"], labels=[l["name"] for l in d.get("labels", [])],
            author=(d.get("author") or {}).get("login", ""), url=d["url"],
            comments=self.comments(n),
        )

    def list_issues(self, label: str) -> list[Issue]:
        rows = self._json(
            "issue", "list", "-R", self.repo, "--label", label, "--state", "open",
            "--limit", "100", "--json", "number,title,body,state,labels,author,url",
        ) or []
        return [
            Issue(
                number=d["number"], title=d["title"], body=d.get("body") or "",
                state=d["state"], labels=[l["name"] for l in d.get("labels", [])],
                author=(d.get("author") or {}).get("login", ""), url=d["url"],
            )
            for d in rows
        ]

    def labeled_by(self, n: int, label: str) -> str | None:
        events = self._api(f"repos/{{repo}}/issues/{n}/events", "--paginate") or []
        actor = None
        for e in events:
            if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == label:
                actor = (e.get("actor") or {}).get("login")
        return actor

    def permission(self, login: str) -> str:
        try:
            d = self._api(f"repos/{{repo}}/collaborators/{login}/permission")
        except GitHubError:
            return "none"
        return (d or {}).get("permission", "none")

    def ensure_labels(self) -> None:
        existing = {
            l["name"]
            for l in (self._json("label", "list", "-R", self.repo, "--limit", "200", "--json", "name") or [])
        }
        for name, (color, desc) in LABELS.items():
            if name not in existing:
                self._run("label", "create", name, "-R", self.repo, "--color", color,
                          "--description", desc, check=False)

    def add_labels(self, n: int, labels: list[str]) -> None:
        self._run("issue", "edit", str(n), "-R", self.repo, "--add-label", ",".join(labels))

    def remove_label(self, n: int, label: str) -> None:
        self._run("issue", "edit", str(n), "-R", self.repo, "--remove-label", label, check=False)

    def assign_self(self, n: int) -> None:
        self._run("issue", "edit", str(n), "-R", self.repo, "--add-assignee", "@me")

    def comments(self, n: int) -> list[Comment]:
        rows = self._api(f"repos/{{repo}}/issues/{n}/comments", "--paginate") or []
        return [
            Comment(
                id=r["id"], author=(r.get("user") or {}).get("login", ""), body=r.get("body") or "",
                created_at=r.get("created_at", ""),
                is_bot=(r.get("user") or {}).get("type") == "Bot",
            )
            for r in rows
        ]

    def create_comment(self, n: int, body: str) -> None:
        self._run("api", f"repos/{self.repo}/issues/{n}/comments", "-X", "POST",
                  "-F", "body=@-", input=body)

    def edit_comment(self, comment_id: int, body: str) -> None:
        self._run("api", f"repos/{self.repo}/issues/comments/{comment_id}", "-X", "PATCH",
                  "-F", "body=@-", input=body)

    # pull requests
    _PR_FIELDS = ("number,url,state,headRefOid,headRefName,baseRefName,body,"
                  "mergeStateStatus,reviewDecision,labels,mergeCommit")

    def _pr_from(self, d: dict[str, Any], with_comments: bool) -> PullRequest:
        return PullRequest(
            number=d["number"], url=d["url"], state=d["state"], head_sha=d["headRefOid"],
            head_branch=d["headRefName"], base=d["baseRefName"], body=d.get("body") or "",
            merge_state=d.get("mergeStateStatus") or "", review_decision=d.get("reviewDecision") or "",
            labels=[l["name"] for l in d.get("labels", [])],
            merge_commit=(d.get("mergeCommit") or {}).get("oid"),
            comments=self.comments(d["number"]) if with_comments else [],
        )

    def pr_for_branch(self, branch: str) -> PullRequest | None:
        rows = self._json("pr", "list", "-R", self.repo, "--head", branch, "--state", "all",
                          "--json", self._PR_FIELDS) or []
        rows = [r for r in rows if r["state"] != "CLOSED"] or rows
        return self._pr_from(rows[0], with_comments=False) if rows else None

    def pr(self, n: int) -> PullRequest:
        d = self._json("pr", "view", str(n), "-R", self.repo, "--json", self._PR_FIELDS)
        return self._pr_from(d, with_comments=True)

    def create_pr(self, branch: str, base: str, title: str, body: str) -> None:
        self._run("pr", "create", "-R", self.repo, "--head", branch, "--base", base,
                  "--title", title, "--body-file", "-", input=body)

    def edit_pr_body(self, n: int, body: str) -> None:
        self._run("pr", "edit", str(n), "-R", self.repo, "--body-file", "-", input=body)

    def checks(self, n: int) -> list[Check]:
        # `gh pr checks` exits non-zero when checks fail or are pending; the JSON is still printed.
        out = self._run("pr", "checks", str(n), "-R", self.repo, "--json", "name,bucket,link",
                        check=False)
        try:
            rows = json.loads(out) if out.strip() else []
        except json.JSONDecodeError:
            raise GitHubError(f"gh pr checks: unexpected output: {out[:200]}") from None
        return [Check(name=r["name"], bucket=r["bucket"], link=r.get("link", "")) for r in rows]

    def failed_log(self, check: Check) -> str:
        m = re.search(r"/actions/runs/(\d+)", check.link or "")
        if not m:
            return f"(no log available for {check.name}; see {check.link})"
        out = self._run("run", "view", m.group(1), "-R", self.repo, "--log-failed", check=False)
        lines = out.splitlines()
        return "\n".join(lines[-200:])

    def merge(self, n: int, sha: str, method: str, subject: str, body: str) -> None:
        self._run("pr", "merge", str(n), "-R", self.repo, f"--{method}",
                  "--match-head-commit", sha, "--subject", subject, "--body", body,
                  "--delete-branch")


def detect_repo(path: str) -> tuple[str, str]:
    """(owner/name, default branch) for the checkout at `path`."""
    p = subprocess.run(
        [_gh_bin(), "repo", "view", "--json", "nameWithOwner,defaultBranchRef"],
        cwd=path, capture_output=True, text=True,
    )
    if p.returncode != 0:
        raise GitHubError(f"not a GitHub repo checkout: {path}: {p.stderr.strip()}")
    d = json.loads(p.stdout)
    return d["nameWithOwner"], (d.get("defaultBranchRef") or {}).get("name") or "main"
