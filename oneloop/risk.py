"""Risk of a change, scored from the diff without a model, then combined with
the reviewer's rating. The higher level wins. Risk decides who must approve:
the author of a change has no say in its risk."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from oneloop import globs, rules
from oneloop.config import Config
from oneloop.gitops import FileChange

LEVELS = ("low", "medium", "high")

TEST_GLOBS = (
    "**/test/**", "**/tests/**", "**/__tests__/**", "**/spec/**",
    "*_test.*", "*.test.*", "*.spec.*", "test_*.py", "*_test.go",
)

# Added lines that skip, focus, sleep or retry tests: the ways a flaky or
# failing test gets hidden instead of fixed.
WEAKENING = [
    (r"\.(skip|only)\(|\bx(it|describe|test)\(", "skips or focuses tests"),
    (r"@pytest\.mark\.(skip|xfail|flaky)|pytest\.skip\(|unittest\.skip", "skips tests"),
    (r"\bt\.Skip(Now|f)?\(|#\[ignore\]|@(Disabled|Ignore)\b", "skips tests"),
    (r"retryTimes|\bretries\s*[:=]|@flaky|\bflaky\(", "retries tests"),
    (r"\bsleep\(|setTimeout\(|time\.sleep|Thread\.sleep|waitForTimeout\(", "adds a sleep"),
]


def rank(level: str) -> int:
    return LEVELS.index(level)


def higher(a: str, b: str) -> str:
    return a if rank(a) >= rank(b) else b


@dataclass
class Reason:
    rule: str
    level: str
    detail: str
    observed_only: bool = False  # the gate is in observe mode: logged, not applied


@dataclass
class Risk:
    level: str = "low"
    reasons: list[Reason] = field(default_factory=list)
    lines: int = 0
    files: int = 0

    def add(self, rule: str, level: str, detail: str, gate_mode: str = "enforce") -> None:
        if gate_mode == "off":
            return
        observed = gate_mode == "observe"
        self.reasons.append(Reason(rule, level, detail, observed_only=observed))
        if not observed:
            self.level = higher(self.level, level)

    def to_json(self) -> dict:
        return asdict(self)


def is_test_file(path: str) -> bool:
    return globs.match_any(path, TEST_GLOBS) is not None


def assess(changes: list[FileChange], added: list[tuple[str, str]], cfg: Config) -> Risk:
    rc = cfg["risk"]
    r = Risk(lines=sum(c.added + c.deleted for c in changes), files=len(changes))

    for c in changes:
        p = globs.match_any(c.path, rules.PROTECTED)
        if p:
            r.add("protected_path", "high",
                  f"{c.path} is part of the rules that judge changes ({p})",
                  cfg.gate("protected_paths"))
        s = globs.match_any(c.path, rc["sensitive"])
        if s:
            r.add("sensitive_path", "high", f"{c.path} matches sensitive pattern {s}")
        m = globs.match_any(c.path, rc["migration_globs"])
        if m:
            r.add("migration", "high", f"{c.path} looks like a migration ({m})")
        d = globs.match_any(c.path, rc["dependency_files"])
        if d:
            r.add("dependencies", "medium", f"{c.path} changes dependencies")
        if is_test_file(c.path):
            if c.status == "D":
                r.add("test_weakening", "medium", f"deletes test file {c.path}", cfg.gate("test_weakening"))
            elif c.deleted > 20 and c.deleted > 2 * c.added:
                r.add("test_weakening", "medium",
                      f"removes {c.deleted} lines from {c.path} while adding {c.added}",
                      cfg.gate("test_weakening"))

    seen: set[tuple[str, str]] = set()
    for path, line in added:
        if not is_test_file(path):
            continue
        for rx, what in WEAKENING:
            if re.search(rx, line) and (path, what) not in seen:
                seen.add((path, what))
                r.add("test_weakening", "medium", f"{path} {what}: {line.strip()[:100]}",
                      cfg.gate("test_weakening"))

    if r.lines > rc["high_lines"]:
        r.add("size", "high", f"{r.lines} lines changed (high above {rc['high_lines']})")
    elif r.lines > rc["medium_lines"]:
        r.add("size", "medium", f"{r.lines} lines changed (medium above {rc['medium_lines']})")
    if r.files > rc["medium_files"]:
        r.add("size", "medium", f"{r.files} files changed (medium above {rc['medium_files']})")
    return r
