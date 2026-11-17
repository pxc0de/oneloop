"""The rules that judge a change, read from the base branch.

A change can never modify the checks that judge it: config, REVIEW.md, the
diff-triggered checklist, the verify skill and per-stage guidance are all read
with `git show origin/<base>:<path>`, never from the worktree. A diff that
touches any of them is high risk and needs a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oneloop import config as config_mod
from oneloop import gitops

CONFIG_PATH = ".oneloop.toml"
REVIEW_PATH = "REVIEW.md"
CHECKLIST_PATH = ".oneloop/checklist.md"
VERIFY_SKILL_DIR = ".claude/skills/verify"
GUIDANCE_DIR = ".oneloop/prompts"

PROTECTED = (
    CONFIG_PATH,
    ".oneloop/**",
    REVIEW_PATH,
    f"{VERIFY_SKILL_DIR}/**",
    ".github/workflows/**",
    ".github/CODEOWNERS",
    "CODEOWNERS",
)


@dataclass(frozen=True)
class Rules:
    base_ref: str  # e.g. origin/main
    base_sha: str
    config: config_mod.Config
    review_md: str
    checklist_md: str
    verify_skill: str
    guidance: dict[str, str]

    def guidance_for(self, stage: str) -> str:
        return self.guidance.get(stage, "")


def load(repo_path: str | Path, base: str, fetch: bool = True) -> Rules:
    if fetch:
        gitops.fetch(repo_path, base)
    ref = f"origin/{base}"
    sha = gitops.rev(repo_path, ref)
    cfg_text = gitops.show(repo_path, sha, CONFIG_PATH)
    cfg = config_mod.load(cfg_text, source=f"base:{ref}@{sha[:7]}")

    verify_parts = []
    for path in gitops.ls_tree(repo_path, sha, VERIFY_SKILL_DIR):
        text = gitops.show(repo_path, sha, path)
        if text is not None and len(text) < 100_000:
            verify_parts.append(f"--- {path} ---\n{text}")

    guidance = {}
    for path in gitops.ls_tree(repo_path, sha, GUIDANCE_DIR):
        if path.endswith(".md"):
            guidance[Path(path).stem] = gitops.show(repo_path, sha, path) or ""

    return Rules(
        base_ref=ref,
        base_sha=sha,
        config=cfg,
        review_md=gitops.show(repo_path, sha, REVIEW_PATH) or "",
        checklist_md=gitops.show(repo_path, sha, CHECKLIST_PATH) or "",
        verify_skill="\n\n".join(verify_parts),
        guidance=guidance,
    )
