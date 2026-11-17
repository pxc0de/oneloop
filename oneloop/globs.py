"""Path globs with `**` support, matched against repo-relative paths.

`*` matches within one path segment, `**/` matches zero or more directories,
and a pattern without a slash matches the file name at any depth
(gitignore-style), so `package.json` also matches `web/package.json`.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    anchored = "/" in pattern.rstrip("/")
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    body = "".join(out)
    if pattern.endswith("/"):
        body += ".*"
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{body}$")


def match(path: str, pattern: str) -> bool:
    return bool(_compile(pattern.lstrip("/")).match(path))


def match_any(path: str, patterns: list[str] | tuple[str, ...]) -> str | None:
    """The first pattern that matches, or None."""
    for p in patterns:
        if match(path, p):
            return p
    return None
