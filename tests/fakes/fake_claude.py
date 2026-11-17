#!/usr/bin/env python3
"""Stands in for `claude -p` in tests. Reads the stage from the prompt's
marker and replays the next scripted response for that stage from the
scenario file named by ONELOOP_FAKE_SCENARIO:

    {"build": [{"output": {...}, "write": {"src/a.py": "..."}, "cost": 0.5}], ...}

An entry may set "subtype" (e.g. "error_max_budget_usd") or "tamper": true
(writes a stray file into the working directory). The last entry repeats.
"""

import json
import os
import re
import sys
import uuid


def main() -> int:
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    m = re.search(r"<!-- oneloop:stage=(\w+) -->", prompt)
    stage = m.group(1) if m else "unknown"
    session = None
    for flag in ("--session-id", "--resume"):
        if flag in argv:
            session = argv[argv.index(flag) + 1]
    session = session or str(uuid.uuid4())

    path = os.environ["ONELOOP_FAKE_SCENARIO"]
    scenario = json.load(open(path))
    counter_path = path + ".calls"
    calls = json.load(open(counter_path)) if os.path.exists(counter_path) else {}
    n = calls.get(stage, 0)
    calls[stage] = n + 1
    calls.setdefault("_log", []).append({"stage": stage, "argv": argv, "resume": "--resume" in argv})
    json.dump(calls, open(counter_path, "w"))

    entries = scenario.get(stage) or [{}]
    entry = entries[min(n, len(entries) - 1)]
    for rel, content in (entry.get("write") or {}).items():
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w") as f:
            f.write(content)
    if entry.get("hang"):
        import time
        time.sleep(entry["hang"])  # a session that goes silent
    if entry.get("tamper"):
        open("stray-file.txt", "w").write("x")

    def out(obj):
        print(json.dumps(obj), flush=True)

    out({"type": "system", "subtype": "init", "session_id": session})
    out({"type": "assistant", "session_id": session, "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": f"echo fake {stage}"}}]}})
    subtype = entry.get("subtype", "success")
    out({
        "type": "result", "subtype": subtype, "is_error": subtype != "success",
        "session_id": session, "total_cost_usd": entry.get("cost", 0.1), "num_turns": 3,
        "duration_ms": 1200, "permission_denials": [],
        "structured_output": entry.get("output") if subtype == "success" else None,
        "result": json.dumps(entry.get("output")), "errors": [] if subtype == "success" else ["fake error"],
    })
    return 0 if subtype == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
