"""What every stage needs: the run state, GitHub, the base-branch rules, a
way to run a Claude session, and a way to stop and hand over to a person."""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterator

from oneloop import claude, rules as rules_mod
from oneloop.config import Config
from oneloop.effects import perform
from oneloop.github import GitHub
from oneloop.notify import notify
from oneloop.state import RunState, StageRecord, emit, now, save, write_json


class Stop(Exception):
    """The loop halts here. The state already says why."""


class RunContext:
    def __init__(self, state: RunState, gh: GitHub, fetch: bool = True):
        self.state = state
        self.gh = gh
        self.fetch = fetch
        self.rules: rules_mod.Rules = rules_mod.load(state.repo_path, state.base, fetch=fetch)
        self._record: StageRecord | None = None

    # --- basics ------------------------------------------------------------
    @property
    def cfg(self) -> Config:
        return self.rules.config

    @property
    def wt(self) -> Path:
        return Path(self.state.worktree)

    @property
    def dir(self) -> Path:
        return self.state.dir

    def refresh_rules(self) -> None:
        """Re-read the rules from the base branch. Called at every stage, so a
        rule changed on main applies from the next stage on."""
        self.rules = rules_mod.load(self.state.repo_path, self.state.base, fetch=self.fetch)

    def save(self) -> None:
        save(self.state)

    def emit(self, type_: str, **data: Any) -> None:
        emit(self.state, type_, **data)

    def artifact(self, name: str) -> Path:
        return self.dir / "artifacts" / name

    def write_artifact(self, name: str, data: Any) -> Path:
        p = self.artifact(name)
        if isinstance(data, str):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data)
        else:
            write_json(p, data)
        return p

    def read_artifact(self, name: str, default: Any = None) -> Any:
        p = self.artifact(name)
        if not p.exists():
            return default
        return json.loads(p.read_text()) if name.endswith(".json") else p.read_text()

    def live(self, activity: str) -> None:
        write_json(self.dir / "live.json", {"ts": now(), "phase": self.state.phase, "activity": activity})

    # --- stage bookkeeping -------------------------------------------------
    @contextlib.contextmanager
    def stage(self, phase: str, round_: int = 0) -> Iterator[StageRecord]:
        self.state.to_phase(phase)
        rec = StageRecord(phase=phase, round=round_, started=now())
        self.state.stages.append(rec)
        self._record = rec
        self.emit("stage.start", round=round_)
        self.live(f"{phase} started")
        self.save()
        try:
            yield rec
            if rec.status == "running":
                rec.status = "done"
        except Stop:
            if rec.status == "running":
                rec.status = "stopped"
            raise
        except Exception as e:
            rec.status = "error"
            rec.note = f"{type(e).__name__}: {e}"[:500]
            raise
        finally:
            rec.ended = now()
            self._record = None
            self.emit("stage.end", status=rec.status, cost_usd=rec.cost_usd,
                      duration_s=round(rec.ended - rec.started, 1), note=rec.note or None)
            self.live(f"{phase} {rec.status}")
            self.save()

    def session(
        self,
        stage: str,
        prompt: str,
        *,
        schema: dict[str, Any] | None,
        budget_key: str | None = None,
        resume: str | None = None,
        **kwargs: Any,
    ) -> claude.SessionResult:
        budget_key = budget_key or stage
        n = len(list((self.dir / "sessions").glob(f"{self.state.phase}-*.jsonl"))) + 1
        log = self.dir / "sessions" / f"{self.state.phase}-{n}-{stage}.jsonl"
        self.emit("session.start", stage=stage, resume=bool(resume), log=log.name)
        started = time.time()
        res = claude.run(
            stage=stage,
            prompt=prompt,
            cwd=self.wt,
            model=self.cfg["models"].get(stage, self.cfg["models"]["build"]),
            budget_usd=float(self.cfg["budget"][budget_key]),
            log_path=log,
            schema=schema,
            resume=resume,
            allow_web=bool(self.cfg["agent"]["allow_web"]),
            permission_mode=self.cfg["agent"]["permission_mode"],
            extra_denied=list(self.cfg["agent"]["disallowed"]),
            on_activity=self.live,
            **kwargs,
        )
        self.state.add_cost(self.state.phase, res.cost_usd)
        if self._record is not None:
            self._record.cost_usd = round(self._record.cost_usd + res.cost_usd, 6)
            self._record.sessions.append(res.session_id)
        self.emit("session.end", stage=stage, ok=res.ok, subtype=res.subtype,
                  cost_usd=res.cost_usd, turns=res.turns, duration_s=round(time.time() - started, 1),
                  denials=len(res.denials), error=res.error[:500] or None, log=log.name,
                  session_id=res.session_id)
        self.save()
        if res.budget_reached:
            self.escalate("budget_reached",
                          f"{stage} session hit its ${self.cfg['budget'][budget_key]:.2f} cap",
                          "Raise budget." + budget_key + " in .oneloop.toml if this run was legitimately large, then resume.")
        return res

    # --- handing over to a person -----------------------------------------
    def escalate(self, kind: str, reason: str, detail: str = "") -> None:
        """Stop the loop and ask a person. Posts one comment on the issue
        (updated in place on later escalations) and notifies. Raises Stop."""
        s = self.state
        s.ask_human(kind, reason, detail)
        self.emit("needs_human", kind=kind, reason=reason, detail=detail[:2000] or None)
        self.save()
        self._label(["oneloop:needs-human"])
        body = (
            f"**oneloop needs a person** ({kind.replace('_', ' ')})\n\n{reason}\n\n"
            + (f"{detail}\n\n" if detail else "")
            + f"Phase: `{s.phase}`. Resume with `oneloop resume {s.issue}` or from the dashboard."
        )
        target = s.pr_number or s.issue
        if kind != "spec_questions":
            self.upsert_comment(target, "needs-human", body)
        notify(f"oneloop · {s.repo}#{s.issue}", f"{kind.replace('_', ' ')}: {reason}")
        raise Stop(kind)

    def upsert_comment(self, number: int, key: str, body: str) -> None:
        marked, existing_id = self.gh.upsert_comment_body(number, key, body)
        digest = hashlib.sha256(body.encode()).hexdigest()[:10]
        effect_key = f"comment:{number}:{key}:{digest}"

        def act() -> None:
            if existing_id:
                self.gh.edit_comment(existing_id, marked)
            else:
                self.gh.create_comment(number, marked)

        def confirm() -> Any:
            c = self.gh.find_comment(number, key)
            return c.id if c and c.body.strip() == marked.strip() else None

        perform(self.state, effect_key, act, confirm)

    def _label(self, labels: list[str]) -> None:
        n = self.state.issue
        key = f"label:{n}:{','.join(labels)}"
        self.state.effects.pop(key, None)  # labels are cheap to re-assert

        def confirm() -> Any:
            have = set(self.gh.issue(n).labels)
            return True if set(labels) <= have else None

        perform(self.state, key, lambda: self.gh.add_labels(n, labels), confirm)

    def label(self, *labels: str) -> None:
        self._label(list(labels))

    def unlabel(self, label: str) -> None:
        self.gh.remove_label(self.state.issue, label)

    # --- feedback for the next build round ---------------------------------
    def add_feedback(self, source: str, items: list[dict[str, Any]]) -> None:
        for i, it in enumerate(items, 1):
            it.setdefault("id", f"{source[:2].upper()}{len(self.state.feedback) + i}")
            it["source"] = source
        self.state.feedback.extend(items)
        self.emit("feedback", source=source, count=len(items))
