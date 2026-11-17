"""Switches a person flips without editing any repo: a global pause and a
per-repo auto-merge override. Read fresh at every decision point."""

from __future__ import annotations

from typing import Any

from oneloop import paths
from oneloop.state import read_json, write_json


def read() -> dict[str, Any]:
    data = read_json(paths.control_path(), {}) or {}
    data.setdefault("paused", False)
    data.setdefault("auto_merge", {})  # repo -> bool (False = forced off)
    return data


def set_paused(paused: bool) -> None:
    data = read()
    data["paused"] = paused
    write_json(paths.control_path(), data)


def set_auto_merge(repo: str, enabled: bool) -> None:
    data = read()
    data["auto_merge"][repo] = enabled
    write_json(paths.control_path(), data)


def auto_merge_allowed(repo: str) -> bool:
    data = read()
    return not data["paused"] and data["auto_merge"].get(repo, True)


def register_repo(repo: str, path: str) -> None:
    data = read_json(paths.repos_path(), {}) or {}
    if data.get(repo) != path:
        data[repo] = path
        write_json(paths.repos_path(), data)


def repos() -> dict[str, str]:
    return read_json(paths.repos_path(), {}) or {}
