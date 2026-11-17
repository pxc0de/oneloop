"""Side effects on the outside world (push, PR, comment, label, merge).

Every effect follows the same four steps: record the intent, do it, confirm it
through a read, record it as done. If the confirmation is unclear the effect is
marked 'maybe' and the run stops; on resume the effect is confirmed first and
only redone if the read shows it never happened. Nothing is retried blindly,
so nothing is posted twice and nothing is recorded that did not land.
"""

from __future__ import annotations

from typing import Any, Callable

from oneloop.state import RunState, emit, now, save


class EffectUncertain(RuntimeError):
    def __init__(self, key: str, detail: str):
        super().__init__(f"could not confirm {key}: {detail}")
        self.key = key
        self.detail = detail


def perform(
    state: RunState,
    key: str,
    act: Callable[[], Any],
    confirm: Callable[[], Any],
) -> Any:
    """Run `act` at most once per key unless `confirm` proves it never landed.

    `confirm` returns a truthy result when the effect is visible, else None.
    Returns the confirmed result.
    """
    rec = state.effects.get(key)
    if rec and rec["status"] == "confirmed":
        return rec["result"]
    if rec and rec["status"] in ("intent", "maybe"):
        found = confirm()
        if found:
            _record(state, key, "confirmed", found)
            return found

    _record(state, key, "intent", None)
    error = ""
    try:
        act()
    except Exception as e:  # the write may still have landed; confirm decides
        error = f"{type(e).__name__}: {e}"
    found = confirm()
    if found:
        _record(state, key, "confirmed", found)
        return found
    _record(state, key, "maybe", None, error)
    raise EffectUncertain(key, error or "the write returned but a read does not show it")


def forget(state: RunState, key: str) -> None:
    """Allow an effect to run again, e.g. a push after new commits."""
    state.effects.pop(key, None)


def _record(state: RunState, key: str, status: str, result: Any, error: str = "") -> None:
    state.effects[key] = {"status": status, "result": result, "at": now(), "error": error}
    emit(state, "effect", key=key, status=status, error=error or None)
    save(state)
