"""In-memory GitHub for tests. PR head tracks the real bare remote's branch."""

from __future__ import annotations

import datetime as dt
import itertools
import subprocess

from oneloop.github import Check, Comment, GitHub, Issue, PullRequest


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class FakeGitHub(GitHub):
    def __init__(self, repo: str, remote_path: str):
        self.repo = repo
        self.remote = remote_path
        self.issues: dict[int, Issue] = {}
        self.prs: dict[int, PullRequest] = {}
        self.pr_comments: dict[int, list[Comment]] = {}
        self.check_results: list[Check] = [Check("ci", "pass")]
        self.ids = itertools.count(1000)
        self.merges: list[dict] = []
        self.writes: list[str] = []
        self.label_events: dict[tuple[int, str], str] = {}
        self.perms: dict[str, str] = {"me": "admin"}

    def add_issue(self, n: int, title: str, body: str, labels=None) -> None:
        self.issues[n] = Issue(n, title, body, "OPEN", list(labels or []), "someone", f"https://x/{n}")

    def whoami(self) -> str:
        return "me"

    def issue(self, n: int) -> Issue:
        i = self.issues[n]
        return Issue(i.number, i.title, i.body, i.state, list(i.labels), i.author, i.url, self.comments(n))

    def list_issues(self, label: str) -> list[Issue]:
        return [self.issue(n) for n, i in self.issues.items() if label in i.labels and i.state == "OPEN"]

    def labeled_by(self, n, label):
        return self.label_events.get((n, label))

    def permission(self, login):
        return self.perms.get(login, "none")

    def ensure_labels(self) -> None:
        pass

    def add_labels(self, n, labels):
        self.writes.append(f"label {n} {labels}")
        for l in labels:
            if l not in self.issues[n].labels:
                self.issues[n].labels.append(l)

    def remove_label(self, n, label):
        if n in self.issues and label in self.issues[n].labels:
            self.issues[n].labels.remove(label)

    def assign_self(self, n):
        pass

    def _target(self, n):
        return self.pr_comments.setdefault(n, [])

    def comments(self, n):
        return list(self._target(n))

    def create_comment(self, n, body):
        self.writes.append(f"comment {n}")
        self._target(n).append(Comment(next(self.ids), "me", body, _now()))

    def edit_comment(self, comment_id, body):
        for cs in self.pr_comments.values():
            for c in cs:
                if c.id == comment_id:
                    c.body = body

    def human_comment(self, n, body, author="alice"):
        self._target(n).append(Comment(next(self.ids), author, body, _now()))

    def _remote_head(self, branch):
        out = subprocess.run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=self.remote,
                             capture_output=True, text=True)
        return out.stdout.strip()

    def _refresh(self, pr):
        if pr.state == "OPEN":
            pr.head_sha = self._remote_head(pr.head_branch)
        return pr

    def pr_for_branch(self, branch):
        for pr in self.prs.values():
            if pr.head_branch == branch:
                return self._refresh(pr)
        return None

    def pr(self, n):
        pr = self._refresh(self.prs[n])
        pr.comments = self.comments(n)
        return pr

    def create_pr(self, branch, base, title, body):
        self.writes.append(f"pr {branch}")
        n = 100 + len(self.prs) + 1
        self.prs[n] = PullRequest(n, f"https://x/pull/{n}", "OPEN", self._remote_head(branch), branch, base, body)

    def edit_pr_body(self, n, body):
        self.prs[n].body = body

    def checks(self, n):
        return list(self.check_results)

    def failed_log(self, check):
        return f"log for {check.name}: AssertionError"

    def merge(self, n, sha, method, subject, body):
        pr = self.pr(n)
        assert pr.head_sha == sha, "merge must be pinned to the head commit"
        self.merges.append({"n": n, "sha": sha, "subject": subject, "body": body})
        pr.state = "MERGED"
        pr.merge_commit = "m" + sha[:6]

    def close_pr(self, n, comment):
        self.prs[n].state = "CLOSED"
        self.create_comment(n, comment)
