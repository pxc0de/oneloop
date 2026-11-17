import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "oneloop" / "hooks" / "stop_checks.py"


def run_hook(checks: dict, counter: str) -> dict | None:
    env = {**os.environ, "ONELOOP_STOP_CHECKS": json.dumps(checks), "ONELOOP_STOP_STATE": counter,
           "ONELOOP_STOP_TIMEOUT": "30"}
    p = subprocess.run([sys.executable, "-I", str(HOOK)], input='{"stop_hook_active": false}',
                       capture_output=True, text=True, env=env, check=True)
    return json.loads(p.stdout) if p.stdout.strip() else None


class StopHookTest(unittest.TestCase):
    def test_green_checks_let_the_builder_stop(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(run_hook({"test": "true"}, f"{d}/n"))

    def test_red_checks_block_with_the_failure_then_give_up_after_three(self):
        with tempfile.TemporaryDirectory() as d:
            checks = {"test": "echo 'AssertionError: 1 != 2'; exit 1"}
            for i in range(3):
                out = run_hook(checks, f"{d}/n")
                self.assertEqual(out["decision"], "block")
                self.assertIn("AssertionError: 1 != 2", out["reason"])
                self.assertIn(f"block {i + 1} of 3", out["reason"])
            self.assertIsNone(run_hook(checks, f"{d}/n"), "the fourth stop goes through")


if __name__ == "__main__":
    unittest.main()
