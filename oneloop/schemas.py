"""JSON schemas for each stage's structured output (`claude -p --json-schema`)."""

from __future__ import annotations

_str = {"type": "string"}
_strs = {"type": "array", "items": _str}
_level = {"type": "string", "enum": ["low", "medium", "high"]}

SPEC = {
    "type": "object",
    "properties": {
        "ready": {"type": "boolean", "description": "False only if an open decision would materially change the implementation."},
        "questions": {**_strs, "description": "Questions for the issue author when ready is false."},
        "summary": {**_str, "description": "One paragraph: what will change and why, from the user's point of view."},
        "acceptance_criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {**_str, "description": "AC1, AC2, ..."},
                    "description": {**_str, "description": "Observable behaviour, from outside the code."},
                    "how_to_verify": {**_str, "description": "Concrete steps that show it working in the real running system."},
                },
                "required": ["id", "description", "how_to_verify"],
            },
        },
        "assumptions": {**_strs, "description": "Defaults chosen where the issue was silent."},
        "plan": {**_strs, "description": "Ordered implementation steps naming files or modules."},
        "test_strategy": {
            "type": "object",
            "properties": {
                "e2e": _str,
                "property": {**_str, "description": "Invariants worth a property test, or empty."},
                "differential": {**_str, "description": "Old-vs-new comparison for refactors/migrations, or empty."},
                "unit": _str,
            },
            "required": ["e2e", "property", "differential", "unit"],
        },
        "out_of_scope": _strs,
        "risk_guess": _level,
        "risk_reason": _str,
    },
    "required": ["ready", "questions", "summary", "acceptance_criteria", "assumptions",
                 "plan", "test_strategy", "out_of_scope", "risk_guess", "risk_reason"],
}

BUILD = {
    "type": "object",
    "properties": {
        "blocked": {"type": "boolean", "description": "True only if you cannot make progress without a person."},
        "blocker": {**_str, "description": "What you need and why, when blocked."},
        "summary": {**_str, "description": "What you changed, for the PR description."},
        "tests_added": _strs,
        "addressed": {**_strs, "description": "Feedback item ids you addressed this round."},
        "not_addressed": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": _str, "why": _str}, "required": ["id", "why"]},
        },
    },
    "required": ["blocked", "blocker", "summary", "tests_added", "addressed", "not_addressed"],
}

_outcome = {"type": "string", "enum": ["pass", "fail", "couldnt_run"]}

VERIFY = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": _str,
                    "outcome": _outcome,
                    "how": {**_str, "description": "What you ran or did."},
                    "observed": {**_str, "description": "What you actually saw. Quote output."},
                    "evidence_files": {**_strs, "description": "Paths under the evidence directory."},
                },
                "required": ["id", "outcome", "how", "observed", "evidence_files"],
            },
        },
        "couldnt_run_reason": {**_str, "description": "Why the system could not be exercised, if any criterion is couldnt_run."},
        "notes": _str,
    },
    "required": ["criteria", "couldnt_run_reason", "notes"],
}

FINDING = {
    "type": "object",
    "properties": {
        "id": {**_str, "description": "F1, F2, ... unique within this round."},
        "file": _str,
        "line": {"type": "integer"},
        "severity": {"type": "string", "enum": ["important", "nit", "pre_existing"]},
        "category": {**_str, "description": "correctness, security, spec, reliability, tests, performance, maintainability"},
        "summary": _str,
        "failure_scenario": {**_str, "description": "Concrete input/state that leads to the wrong result."},
        "checklist_item": {**_str, "description": "Checklist item id this violates, or empty."},
    },
    "required": ["id", "file", "line", "severity", "category", "summary", "failure_scenario", "checklist_item"],
}

REVIEW = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": FINDING},
        "previous": {
            "type": "array",
            "description": "One entry per finding from earlier rounds.",
            "items": {
                "type": "object",
                "properties": {"id": _str, "resolved": {"type": "boolean"}, "note": _str},
                "required": ["id", "resolved", "note"],
            },
        },
        "checklist": {
            "type": "array",
            "description": "One entry per checklist item you were given.",
            "items": {
                "type": "object",
                "properties": {"id": _str, "ok": {"type": "boolean"}, "note": _str},
                "required": ["id", "ok", "note"],
            },
        },
        "risk": {
            "type": "object",
            "properties": {"level": _level, "reason": _str},
            "required": ["level", "reason"],
        },
        "summary": _str,
    },
    "required": ["findings", "previous", "checklist", "risk", "summary"],
}

VALIDATE = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": _str,
                    "verdict": {"type": "string", "enum": ["confirmed", "rejected", "uncertain"]},
                    "severity": {"type": "string", "enum": ["important", "nit", "pre_existing"]},
                    "reason": _str,
                },
                "required": ["id", "verdict", "severity", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

BRIEF = {
    "type": "object",
    "properties": {
        "headline": {**_str, "description": "One sentence: what merging this does."},
        "what_changed": _strs,
        "evidence": {**_strs, "description": "What was observed working, with criterion ids."},
        "risks": {**_strs, "description": "What could go wrong, most likely first."},
        "look_at": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": _str, "lines": _str, "why": _str},
                "required": ["file", "lines", "why"],
            },
        },
        "recommendation": {"type": "string", "enum": ["approve", "send_back", "take_over"]},
        "recommendation_reason": _str,
    },
    "required": ["headline", "what_changed", "evidence", "risks", "look_at",
                 "recommendation", "recommendation_reason"],
}

LEARN = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": _str,
                    "title": _str,
                    "triggers": {**_strs, "description": "'path <glob>' or 'added /regex/'"},
                    "check": _str,
                    "origin": {**_str, "description": "The PRs/issues this pattern came from (at least two)."},
                },
                "required": ["id", "title", "triggers", "check", "origin"],
            },
        },
        "prune": {
            "type": "array",
            "items": {"type": "object", "properties": {"id": _str, "why": _str}, "required": ["id", "why"]},
        },
        "notes": _str,
    },
    "required": ["rules", "prune", "notes"],
}

INIT = {
    "type": "object",
    "properties": {
        "files_written": _strs,
        "checks_verified": {
            "type": "object",
            "description": "The check commands you ran successfully; empty string if none.",
            "properties": {"lint": _str, "typecheck": _str, "test": _str, "build": _str},
            "required": ["lint", "typecheck", "test", "build"],
        },
        "verify_summary": {**_str, "description": "How the verify skill starts and drives the app."},
        "notes": _str,
    },
    "required": ["files_written", "checks_verified", "verify_summary", "notes"],
}

SMOKE = {
    "type": "object",
    "properties": {
        "outcome": _outcome,
        "observed": _str,
        "broken_step": {**_str, "description": "The step that did not work as written, or empty."},
    },
    "required": ["outcome", "observed", "broken_step"],
}
