"""Tell a person when, and only when, they are needed."""

from __future__ import annotations

import os
import shutil
import subprocess


def notify(title: str, message: str) -> None:
    if os.environ.get("ONELOOP_NO_NOTIFY"):
        return
    if shutil.which("osascript"):
        script = f"display notification {_q(message)} with title {_q(title)} sound name \"Glass\""
        subprocess.run(["osascript", "-e", script], capture_output=True)
    elif shutil.which("notify-send"):
        subprocess.run(["notify-send", title, message], capture_output=True)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"')[:240] + '"'
