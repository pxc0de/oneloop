"""Per-repo configuration (.oneloop.toml).

The file is always read from the base branch, never from the change being
judged, so a change cannot loosen the rules that gate it. Unknown keys are
rejected so a typo does not silently fall back to a default.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from typing import Any

GATE_MODES = ("off", "observe", "enforce")
RISK_LEVELS = ("low", "medium", "high")
AUTO_MERGE_LEVELS = ("off", "low", "medium")

DEFAULTS: dict[str, Any] = {
    "base": "",  # empty: use the repo's default branch
    "checks": {
        "lint": "",
        "typecheck": "",
        "test": "",
        "build": "",
        "timeout_s": 900,
        # Checks the builder's Stop hook runs before it may finish.
        "stop_hook": ["lint", "typecheck", "test"],
    },
    "verify": {
        # Give the verifier Claude in Chrome for driving web UIs.
        "chrome": False,
        # Extra tool rules for the verifier, e.g. an MCP server for a simulator.
        "allowed": [],
    },
    "ci": {
        # "required": zero CI checks on the PR is couldn't-run, not green.
        # "none": the repo has no CI; skip the CI stage.
        "mode": "required",
        "timeout_min": 30,
    },
    "risk": {
        "sensitive": [],
        "medium_lines": 200,
        "high_lines": 1000,
        "medium_files": 15,
        "dependency_files": [
            "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
            "bun.lockb", "requirements*.txt", "pyproject.toml", "poetry.lock",
            "uv.lock", "Pipfile*", "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
            "Gemfile*", "composer.*",
        ],
        "migration_globs": ["**/migrations/**", "**/migrate/**", "**/*.sql"],
    },
    "land": {
        # Highest risk level that merges without a human. "off" disables it.
        "auto_merge": "low",
        "merge_method": "squash",
    },
    "caps": {
        "checks_rounds": 3,
        "verify_rounds": 3,
        "review_rounds": 3,
        "ci_rounds": 2,
    },
    # Per-session USD caps. Start at 3-5x a normal run and tighten from the ledger.
    "budget": {
        "spec": 3.0,
        "build": 15.0,
        "fix": 8.0,
        "verify": 5.0,
        "review": 6.0,
        "validate": 3.0,
        "brief": 1.0,
    },
    "models": {
        "spec": "opus",
        "build": "opus",
        "verify": "sonnet",
        "review": "opus",
        "validate": "sonnet",
        "brief": "sonnet",
    },
    "gates": {
        "verify": "enforce",
        "checklist": "enforce",
        "protected_paths": "enforce",
        "test_weakening": "enforce",
    },
    "labels": {"trigger": "oneloop"},
    "agent": {
        "allow_web": False,
        # Extra tool rules denied to every agent session, e.g. "Bash(terraform apply:*)".
        "disallowed": [],
        # Extra tool rules allowed to the builder, e.g. "mcp__playwright".
        "allowed": [],
    },
}


class ConfigError(ValueError):
    pass


def _merge(base: dict, override: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        where = f"{path}{key}"
        if key not in base:
            raise ConfigError(f"unknown config key: {where}")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{where} must be a table")
            out[key] = _merge(base[key], value, where + ".")
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    source: str  # "base:<ref>" or "defaults"

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def gate(self, name: str) -> str:
        return self.raw["gates"][name]

    def check_commands(self) -> dict[str, str]:
        checks = self.raw["checks"]
        return {k: checks[k] for k in ("lint", "typecheck", "test", "build")}


def load(text: str | None, source: str = "defaults") -> Config:
    data: dict[str, Any] = {}
    if text:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f".oneloop.toml: {e}") from e
    raw = _merge(DEFAULTS, data)
    for name, mode in raw["gates"].items():
        if mode not in GATE_MODES:
            raise ConfigError(f"gates.{name} must be one of {GATE_MODES}, got {mode!r}")
    if raw["land"]["auto_merge"] not in AUTO_MERGE_LEVELS:
        raise ConfigError(f"land.auto_merge must be one of {AUTO_MERGE_LEVELS}")
    if raw["ci"]["mode"] not in ("required", "none"):
        raise ConfigError("ci.mode must be 'required' or 'none'")
    return Config(raw=raw, source=source if text else "defaults")
