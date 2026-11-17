import os
import tempfile
import unittest

from oneloop import checklist, config, globs, risk, state
from oneloop.effects import EffectUncertain, perform
from oneloop.gitops import FileChange, added_lines


class TempHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("ONELOOP_HOME")
        os.environ["ONELOOP_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("ONELOOP_HOME", None)
        else:
            os.environ["ONELOOP_HOME"] = self._old
        self._tmp.cleanup()


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = config.load(None)
        self.assertEqual(cfg.source, "defaults")
        self.assertEqual(cfg["land"]["auto_merge"], "low")

    def test_override_merges(self):
        cfg = config.load('[checks]\ntest = "pytest -q"\n', source="base:x")
        self.assertEqual(cfg["checks"]["test"], "pytest -q")
        self.assertEqual(cfg["checks"]["timeout_s"], 900)

    def test_unknown_key_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load("[checks]\ntets = 'x'\n")

    def test_bad_gate_mode_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load("[gates]\nverify = 'sometimes'\n")


class GlobTest(unittest.TestCase):
    def test_patterns(self):
        cases = [
            ("src/auth/login.ts", "src/auth/**", True),
            ("src/auth", "src/auth/**", False),
            ("web/package.json", "package.json", True),
            ("db/migrations/001.sql", "**/migrations/**", True),
            ("migrations/001.sql", "**/migrations/**", True),
            ("src/a.test.ts", "*.test.*", True),
            ("src/a.ts", "*.test.*", False),
            (".github/workflows/ci.yml", ".github/workflows/**", True),
            ("x/.github/workflows/ci.yml", ".github/workflows/**", False),
        ]
        for path, pat, want in cases:
            self.assertEqual(globs.match(path, pat), want, (path, pat))


CHECKLIST = """
# Reliability checklist

## RL-001: Session fields load from old transcripts
trigger: path src/session/**, added /JSON\\.parse\\(/
check: Confirm a new field loads safely when older transcripts
  lack it.
origin: #482

## RL-002: Observe only
trigger: path docs/**
check: docs say what the code does
mode: observe
"""


class ChecklistTest(unittest.TestCase):
    def test_parse(self):
        items = checklist.parse(CHECKLIST)
        self.assertEqual([i.id for i in items], ["RL-001", "RL-002"])
        self.assertIn("lack it.", items[0].check)
        self.assertEqual(len(items[0].triggers), 2)
        self.assertEqual(items[1].mode, "observe")

    def test_match_by_path_and_added_line(self):
        items = checklist.parse(CHECKLIST)
        m = checklist.match(items, ["src/session/store.ts"], [])
        self.assertEqual([x.item.id for x in m], ["RL-001"])
        m = checklist.match(items, ["src/x.ts"], [("src/x.ts", "const a = JSON.parse(s)")])
        self.assertEqual([x.item.id for x in m], ["RL-001"])
        self.assertEqual(checklist.match(items, ["src/x.ts"], [("src/x.ts", "a")]), [])

    def test_item_without_trigger_rejected(self):
        with self.assertRaises(checklist.ChecklistError):
            checklist.parse("## RL-9: nothing\ncheck: x\n")


class RiskTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load('[risk]\nsensitive = ["src/billing/**"]\n')

    def test_small_change_is_low(self):
        r = risk.assess([FileChange("src/a.py", 10, 2, "M")], [], self.cfg)
        self.assertEqual(r.level, "low")

    def test_protected_path_is_high(self):
        r = risk.assess([FileChange("REVIEW.md", 1, 0, "M")], [], self.cfg)
        self.assertEqual(r.level, "high")
        self.assertEqual(r.reasons[0].rule, "protected_path")

    def test_protected_path_observe_mode_logs_only(self):
        cfg = config.load('[gates]\nprotected_paths = "observe"\n')
        r = risk.assess([FileChange(".oneloop.toml", 1, 0, "M")], [], cfg)
        self.assertEqual(r.level, "low")
        self.assertTrue(r.reasons[0].observed_only)

    def test_sensitive_and_deps(self):
        r = risk.assess([FileChange("package.json", 1, 1, "M")], [], self.cfg)
        self.assertEqual(r.level, "medium")
        r = risk.assess([FileChange("src/billing/charge.ts", 1, 1, "M")], [], self.cfg)
        self.assertEqual(r.level, "high")

    def test_skipped_test_is_weakening(self):
        added = [("tests/test_a.py", "@pytest.mark.skip(reason='flaky')")]
        r = risk.assess([FileChange("tests/test_a.py", 1, 0, "M")], added, self.cfg)
        self.assertEqual(r.level, "medium")
        self.assertEqual(r.reasons[0].rule, "test_weakening")

    def test_size(self):
        r = risk.assess([FileChange("src/a.py", 600, 500, "M")], [], self.cfg)
        self.assertEqual(r.level, "high")


class DiffParseTest(unittest.TestCase):
    def test_added_lines(self):
        d = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n a\n+b\n-c\n"
        self.assertEqual(added_lines(d), [("x", "b")])


class StateTest(TempHome):
    def test_roundtrip(self):
        s = state.RunState(repo="o/r", issue=7, repo_path="/tmp/r")
        s.ask_human("approval", "medium risk")
        state.save(s)
        back = state.load("o/r", 7)
        self.assertEqual(back.needs_human.kind, "approval")
        back.resume()
        self.assertEqual(back.status, "running")
        self.assertEqual(back.human_touches, 1)

    def test_invariant(self):
        with self.assertRaises(ValueError):
            state.RunState(repo="o/r", issue=1, repo_path="/", status="needs_human")
        with self.assertRaises(ValueError):
            state.NeedsHuman(kind="bored", reason="")

    def test_lock_and_liveness(self):
        s = state.RunState(repo="o/r", issue=3, repo_path="/")
        state.save(s)
        self.assertEqual(state.liveness(s), "stale")
        with state.RunLock(s.dir):
            self.assertEqual(state.liveness(s), "active")
        self.assertEqual(state.liveness(s), "stale")


class EffectsTest(TempHome):
    def test_act_once_then_cached(self):
        s = state.RunState(repo="o/r", issue=1, repo_path="/")
        world = []
        r = perform(s, "comment", lambda: world.append(1), lambda: len(world) or None)
        self.assertEqual(r, 1)
        perform(s, "comment", lambda: world.append(1), lambda: len(world) or None)
        self.assertEqual(world, [1])

    def test_unconfirmed_is_maybe_and_reconciled_without_redo(self):
        s = state.RunState(repo="o/r", issue=1, repo_path="/")
        world = []

        def act():
            world.append("posted")
            raise TimeoutError("network")

        visible = {"yes": False}
        with self.assertRaises(EffectUncertain):
            perform(s, "pr", act, lambda: "pr#1" if visible["yes"] else None)
        self.assertEqual(s.effects["pr"]["status"], "maybe")
        # Later the read shows it did land: confirm without acting again.
        visible["yes"] = True
        self.assertEqual(perform(s, "pr", act, lambda: "pr#1"), "pr#1")
        self.assertEqual(world, ["posted"])


if __name__ == "__main__":
    unittest.main()
