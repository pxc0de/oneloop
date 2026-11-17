"""The reliability checklist: items whose trigger can be read from a diff.

The orchestrator matches triggers itself, without a model, and hands the
reviewer only the items that fire (usually a handful), so nobody has to
remember past outages. Format of .oneloop/checklist.md:

    ## RL-001: New session fields load safely
    trigger: path src/session/**, path src/messages/**
    trigger: added /JSON\\.parse\\(/
    check: When session storage changes, confirm a new field loads from old
      transcripts that lack it.
    origin: #482
    mode: enforce

`path <glob>` fires when a changed file matches; `added /regex/` fires when an
added line matches. Indented lines continue the previous field. `mode` is
off|observe|enforce and defaults to enforce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from oneloop import globs
from oneloop.config import GATE_MODES


@dataclass
class Trigger:
    kind: str  # path | added
    value: str

    def fires(self, changed_paths: list[str], added: list[tuple[str, str]]) -> str | None:
        if self.kind == "path":
            for p in changed_paths:
                if globs.match(p, self.value):
                    return p
            return None
        rx = re.compile(self.value)
        for path, line in added:
            if rx.search(line):
                return f"{path}: {line.strip()[:120]}"
        return None


@dataclass
class Item:
    id: str
    title: str
    check: str = ""
    origin: str = ""
    mode: str = "enforce"
    triggers: list[Trigger] = field(default_factory=list)


class ChecklistError(ValueError):
    pass


def parse(text: str) -> list[Item]:
    items: list[Item] = []
    current: Item | None = None
    last_key = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            head = line[3:].strip()
            ident, _, title = head.partition(":")
            current = Item(id=ident.strip(), title=title.strip())
            items.append(current)
            last_key = ""
            continue
        if current is None or not line.strip():
            continue
        if raw[:1] in (" ", "\t") and last_key in ("check", "origin"):
            setattr(current, last_key, (getattr(current, last_key) + " " + line.strip()).strip())
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not sep:
            continue
        last_key = key
        if key == "trigger":
            for part in _split_triggers(value):
                current.triggers.append(_parse_trigger(part, current.id))
        elif key == "check":
            current.check = value
        elif key == "origin":
            current.origin = value
        elif key == "mode":
            if value not in GATE_MODES:
                raise ChecklistError(f"{current.id}: mode must be one of {GATE_MODES}")
            current.mode = value
    for it in items:
        if not it.triggers:
            raise ChecklistError(f"{it.id}: needs at least one trigger readable from a diff")
    return items


def _split_triggers(value: str) -> list[str]:
    # Commas separate triggers, except inside /regex/.
    parts, buf, in_rx = [], "", False
    for ch in value:
        if ch == "/":
            in_rx = not in_rx
        if ch == "," and not in_rx:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _parse_trigger(part: str, item_id: str) -> Trigger:
    kind, _, value = part.partition(" ")
    value = value.strip()
    if kind == "path" and value:
        return Trigger("path", value)
    if kind == "added" and len(value) >= 2 and value.startswith("/") and value.endswith("/"):
        pattern = value[1:-1]
        try:
            re.compile(pattern)
        except re.error as e:
            raise ChecklistError(f"{item_id}: bad regex {value}: {e}") from e
        return Trigger("added", pattern)
    raise ChecklistError(f"{item_id}: trigger must be 'path <glob>' or 'added /regex/', got {part!r}")


@dataclass
class Match:
    item: Item
    because: str


def match(items: list[Item], changed_paths: list[str], added: list[tuple[str, str]]) -> list[Match]:
    out = []
    for it in items:
        if it.mode == "off":
            continue
        for t in it.triggers:
            why = t.fires(changed_paths, added)
            if why:
                out.append(Match(it, why))
                break
    return out
