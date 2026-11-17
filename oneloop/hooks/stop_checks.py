"""Claude Code Stop hook for the BUILD session.

When the builder tries to finish, run the repo's cheap checks. If any fail,
block the stop and hand the failure back so the builder keeps working. After
three blocks in one session the stop is let through so the builder cannot get
stuck; the orchestrator runs the checks again itself either way.

Configured through env vars set by the orchestrator:
  ONELOOP_STOP_CHECKS  JSON {name: command}
  ONELOOP_STOP_STATE   path of a counter file for this session
  ONELOOP_STOP_TIMEOUT seconds per check
"""

import json
import os
import subprocess
import sys

MAX_BLOCKS = 3


def main() -> int:
    try:
        json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        pass
    checks = json.loads(os.environ.get("ONELOOP_STOP_CHECKS") or "{}")
    counter_path = os.environ.get("ONELOOP_STOP_STATE") or ""
    timeout = int(os.environ.get("ONELOOP_STOP_TIMEOUT") or "900")
    if not checks or not counter_path:
        return 0
    try:
        blocks = int(open(counter_path).read().strip() or 0)
    except (FileNotFoundError, ValueError):
        blocks = 0
    if blocks >= MAX_BLOCKS:
        return 0

    failures = []
    for name, cmd in checks.items():
        if not cmd:
            continue
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            failures.append(f"`{cmd}` timed out after {timeout}s")
            break
        if p.returncode != 0:
            out = ((p.stdout or "") + "\n" + (p.stderr or "")).strip().splitlines()[-60:]
            failures.append(f"`{cmd}` exited {p.returncode}:\n" + "\n".join(out))
            break
    if not failures:
        return 0

    with open(counter_path, "w") as f:
        f.write(str(blocks + 1))
    reason = (
        f"The repo's checks fail (block {blocks + 1} of {MAX_BLOCKS}). Fix the root cause before "
        "finishing; do not skip, sleep or retry tests.\n\n" + "\n\n".join(failures)
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
