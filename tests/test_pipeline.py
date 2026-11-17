"""End-to-end runs of the loop against a real git repo with a local bare
remote, a fake `claude` and an in-memory GitHub."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from oneloop import control, ledger, pipeline, state
from tests.fakes.fake_github import FakeGitHub

FAKE_CLAUDE = Path(__file__).parent / "fakes" / "fake_claude.py"

SPEC_OK = {
    "ready": True, "questions": [], "summary": "Add a greet function.",
    "acceptance_criteria": [{"id": "AC1", "description": "greet prints hello",
                             "how_to_verify": "python -c 'import app; app.greet()'"}],
    "assumptions": [], "plan": ["edit app.py"],
    "test_strategy": {"e2e": "run it", "property": "", "differential": "", "unit": "test greet"},
    "out_of_scope": [], "risk_guess": "low", "risk_reason": "tiny",
}
BUILD_OK = {"blocked": False, "blocker": "", "summary": "Added greet().", "tests_added": [],
            "addressed": [], "not_addressed": [], "protected_edits": []}
VERIFY_PASS = {"criteria": [{"id": "AC1", "outcome": "pass", "how": "ran it", "observed": "hello",
                             "evidence_files": []}], "couldnt_run_reason": "", "notes": ""}
VERIFY_CANT = {"criteria": [{"id": "AC1", "outcome": "couldnt_run", "how": "tried", "observed": "no python",
                             "evidence_files": []}], "couldnt_run_reason": "interpreter missing", "notes": ""}
REVIEW_CLEAN = {"findings": [], "previous": [], "checklist": [], "risk": {"level": "low", "reason": "small"},
                "summary": "Looks right."}
FINDING = {"id": "F1", "file": "app.py", "line": 1, "severity": "important", "category": "correctness",
           "summary": "prints helo", "failure_scenario": "greet() prints 'helo'", "checklist_item": ""}
REVIEW_BLOCK = {**REVIEW_CLEAN, "findings": [FINDING]}
REVIEW_R2 = {**REVIEW_CLEAN, "previous": [{"id": "F1", "resolved": True, "note": "fixed"}]}
VALIDATE_CONFIRM = {"verdicts": [{"id": "F1", "verdict": "confirmed", "severity": "important", "reason": "ran it"}]}
BRIEF = {"headline": "Adds greet", "what_changed": ["app.py"], "evidence": ["AC1 pass"], "risks": [],
         "look_at": [], "recommendation": "approve", "recommendation_reason": "verified"}


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env_backup = dict(os.environ)
        os.environ.update({
            "ONELOOP_HOME": str(root / "home"),
            "ONELOOP_CLAUDE_BIN": str(FAKE_CLAUDE),
            "ONELOOP_FAKE_SCENARIO": str(root / "scenario.json"),
            "ONELOOP_NO_NOTIFY": "1",
            "ONELOOP_CI_POLL_S": "0",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
        })
        os.chmod(FAKE_CLAUDE, 0o755)
        self.remote = root / "remote.git"
        sh(root, "git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.repo = root / "repo"
        sh(root, "git", "clone", "-q", str(self.remote), str(self.repo))
        (self.repo / "app.py").write_text("def greet():\n    pass\n")
        (self.repo / ".oneloop.toml").write_text('[checks]\ntest = "python3 -c \\"import app\\""\n')
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "init")
        sh(self.repo, "git", "push", "-q", "origin", "HEAD:main")
        self.gh = FakeGitHub("o/r", str(self.remote))
        self.gh.add_issue(1, "Add greet", "Please add a greet function that prints hello.")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        self.tmp.cleanup()

    def scenario(self, **stages):
        Path(os.environ["ONELOOP_FAKE_SCENARIO"]).write_text(json.dumps(stages))

    def calls(self):
        p = Path(os.environ["ONELOOP_FAKE_SCENARIO"] + ".calls")
        return json.loads(p.read_text()) if p.exists() else {}

    def run_loop(self):
        s = pipeline.start("o/r", str(self.repo), 1, self.gh, "main")
        return pipeline.drive(s, self.gh)

    def build(self, text="def greet():\n    print('hello')\n"):
        return {"output": BUILD_OK, "write": {"app.py": text}}

    def test_happy_path_auto_merges_low_risk(self):
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_PASS}],
                      review=[{"output": REVIEW_CLEAN}])
        s = self.run_loop()
        self.assertEqual(s.status, "merged", s.needs_human)
        self.assertEqual(len(self.gh.merges), 1)
        merge = self.gh.merges[0]
        self.assertEqual(merge["sha"], s.head_sha)
        self.assertIn("Oneloop-Verified: verify e2e 1/1", merge["body"])
        self.assertIn("merge auto", merge["body"])
        self.assertIn("Closes #1", self.gh.prs[101].body)
        self.assertEqual([r["outcome"] for r in ledger.read()], ["merged"])
        self.assertGreater(s.total_cost, 0)
        # Only one PR, even though SHIP ran through the effect machinery.
        self.assertEqual(len(self.gh.prs), 1)

    def test_review_finding_goes_back_to_builder_and_resumes_its_session(self):
        self.scenario(
            spec=[{"output": SPEC_OK}],
            build=[self.build("def greet():\n    print('helo')\n"), self.build()],
            verify=[{"output": VERIFY_PASS}],
            review=[{"output": REVIEW_BLOCK}, {"output": REVIEW_R2}],
            validate=[{"output": VALIDATE_CONFIRM}],
        )
        s = self.run_loop()
        self.assertEqual(s.status, "merged", s.needs_human)
        self.assertEqual(s.rounds["review"], 1)
        builds = [c for c in self.calls()["_log"] if c["stage"] == "build"]
        self.assertFalse(builds[0]["resume"])
        self.assertTrue(builds[1]["resume"], "fix rounds resume the builder's own session")
        # The second review only sees the diff since the first review.
        r2 = json.loads((s.dir / "artifacts" / "review-2.json").read_text())
        self.assertEqual(r2["blocking"], [])

    def test_verify_couldnt_run_stops_for_a_person(self):
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_CANT}])
        s = self.run_loop()
        self.assertEqual(s.status, "needs_human")
        self.assertEqual(s.needs_human.kind, "couldnt_run")
        self.assertEqual(self.gh.merges, [])
        self.assertIn("oneloop:needs-human", self.gh.issues[1].labels)

    def test_verifier_that_edits_the_worktree_is_discarded(self):
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()],
                      verify=[{"output": VERIFY_PASS, "tamper": True}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "couldnt_run")
        self.assertIn("modified files", s.needs_human.reason)
        self.assertFalse((Path(s.worktree) / "stray-file.txt").exists())

    def test_ambiguous_issue_posts_questions(self):
        spec = {**SPEC_OK, "ready": False, "questions": ["Hello or hi?"]}
        self.scenario(spec=[{"output": spec}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "spec_questions")
        self.assertTrue(any("Hello or hi?" in c.body for c in self.gh.comments(1)))
        # The answer arrives; resume re-runs SPEC with it.
        self.gh.human_comment(1, "hello")
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_PASS}],
                      review=[{"output": REVIEW_CLEAN}])
        Path(os.environ["ONELOOP_FAKE_SCENARIO"] + ".calls").unlink()
        s = pipeline.drive(pipeline.resume(s, self.gh), self.gh)
        self.assertEqual(s.status, "merged")
        self.assertEqual(s.human_touches, 1)

    def test_protected_path_needs_approval_then_merges(self):
        self.scenario(spec=[{"output": SPEC_OK}],
                      build=[{"output": BUILD_OK, "write": {"app.py": "def greet():\n    print('hello')\n",
                                                            "REVIEW.md": "loosened"}}],
                      verify=[{"output": VERIFY_PASS}], review=[{"output": REVIEW_CLEAN}],
                      brief=[{"output": BRIEF}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "approval")
        self.assertEqual(s.risk["level"], "high")
        self.assertTrue(any("oneloop brief" in c.body for c in self.gh.comments(101)))
        s = pipeline.drive(pipeline.approve(s, self.gh), self.gh)
        self.assertEqual(s.status, "merged")
        self.assertIn("merge human", self.gh.merges[0]["body"])

    def test_builder_proposes_a_rules_edit_and_a_person_approves(self):
        edit = {"path": ".claude/skills/verify/SKILL.md", "content": "# verify\nrun app.greet()\n", "why": "new output"}
        sneaky = {"path": "app.py", "content": "evil", "why": "not protected"}
        self.scenario(spec=[{"output": SPEC_OK}],
                      build=[{"output": {**BUILD_OK, "protected_edits": [edit, sneaky]},
                              "write": {"app.py": "def greet():\n    print('hello')\n"}}],
                      verify=[{"output": VERIFY_PASS}], review=[{"output": REVIEW_CLEAN}], brief=[{"output": BRIEF}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "approval")
        self.assertEqual(s.risk["level"], "high")
        wt = Path(s.worktree)
        self.assertEqual((wt / ".claude/skills/verify/SKILL.md").read_text(), edit["content"])
        self.assertIn("print('hello')", (wt / "app.py").read_text(), "non-protected paths are refused")
        events = [e["type"] for e in state.read_events(s.dir)]
        self.assertIn("protected_edit.refused", events)

    def test_paused_blocks_auto_merge(self):
        control.set_paused(True)
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_PASS}],
                      review=[{"output": REVIEW_CLEAN}], brief=[{"output": BRIEF}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "approval")
        self.assertIn("paused", s.needs_human.reason)

    def test_new_human_comment_on_pr_stops_merge(self):
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_PASS}],
                      review=[{"output": REVIEW_CLEAN}])
        s = pipeline.start("o/r", str(self.repo), 1, self.gh, "main")
        for phase in ("spec", "build", "checks", "verify", "review", "ship", "ci"):
            s = pipeline.drive(s, self.gh, only=phase)
        self.gh.human_comment(101, "wait, this should also handle names")
        s = pipeline.drive(s, self.gh, only="land")
        self.assertEqual(s.needs_human.kind, "changed_under_us")
        self.assertEqual(self.gh.merges, [])

    def test_failing_check_goes_back_to_builder_then_caps(self):
        self.scenario(spec=[{"output": SPEC_OK}],
                      build=[self.build(f"def greet(:\n    {i}\n") for i in range(4)])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "cap_reached")
        self.assertEqual(s.rounds["build"], 4)  # first build + 3 fix rounds

    def test_budget_reached_is_its_own_state(self):
        self.scenario(spec=[{"subtype": "error_max_budget_usd"}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "budget_reached")

    def test_conflict_with_base_goes_to_the_builder_not_a_person(self):
        self.scenario(spec=[{"output": SPEC_OK}],
                      build=[self.build(), self.build("def greet():\n    print('hello, world')\n")],
                      verify=[{"output": VERIFY_PASS}], review=[{"output": REVIEW_CLEAN}])
        s = pipeline.start("o/r", str(self.repo), 1, self.gh, "main")
        for phase in ("spec", "build", "checks", "verify", "review", "ship", "ci"):
            s = pipeline.drive(s, self.gh, only=phase)
        # Another change lands on the same line first.
        (self.repo / "app.py").write_text("def greet():\n    print('hi')\n")
        sh(self.repo, "git", "commit", "-qam", "hi")
        sh(self.repo, "git", "push", "-q", "origin", "HEAD:main")
        s = pipeline.drive(s, self.gh)
        self.assertEqual(s.status, "merged", s.needs_human)
        self.assertEqual(s.rounds["conflict"], 1)
        builds = [c for c in self.calls()["_log"] if c["stage"] == "build"]
        self.assertTrue(builds[-1]["resume"], "the builder's own session resolves the conflict")
        log = subprocess.run(["git", "log", "--format=%s", "-3", "refs/heads/oneloop/1-add-greet"],
                             cwd=self.remote, capture_output=True, text=True).stdout
        self.assertIn("merge origin/main", log)

    def test_cancel_closes_the_pr_and_a_rerun_starts_fresh(self):
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_CANT}])
        s = self.run_loop()
        self.assertEqual(s.needs_human.kind, "couldnt_run")
        s.pr_number = None
        pipeline.cancel(s, self.gh)
        again = pipeline.start("o/r", str(self.repo), 1, self.gh, "main")
        self.assertEqual(again.status, "running")
        self.assertEqual(again.phase, "spec")
        self.assertTrue(any(p.name.startswith("1.cancelled-") for p in s.dir.parent.iterdir()))

    def test_silent_hung_session_is_killed_by_the_watchdog(self):
        from oneloop import claude
        self.scenario(spec=[{"hang": 30}])
        res = claude.run(stage="spec", prompt="<!-- oneloop:stage=spec -->", cwd=self.repo, model="x",
                         budget_usd=1, log_path=Path(self.tmp.name) / "s.jsonl", timeout_s=1)
        self.assertEqual(res.subtype, "timeout")
        self.assertIn("killed after", res.error)

    def test_base_moved_rebases_and_reverifies(self):
        self.scenario(spec=[{"output": SPEC_OK}], build=[self.build()], verify=[{"output": VERIFY_PASS}],
                      review=[{"output": REVIEW_CLEAN}])
        s = pipeline.start("o/r", str(self.repo), 1, self.gh, "main")
        for phase in ("spec", "build", "checks", "verify", "review", "ship", "ci"):
            s = pipeline.drive(s, self.gh, only=phase)
        # Someone lands an unrelated change on main.
        (self.repo / "README.md").write_text("hi\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-q", "-m", "readme")
        sh(self.repo, "git", "push", "-q", "origin", "HEAD:main")
        s = pipeline.drive(s, self.gh)
        self.assertEqual(s.status, "merged", s.needs_human)
        events = [e["type"] for e in state.read_events(s.dir)]
        self.assertIn("rebased", events)
        self.assertIn("review.carried_over", events, "same patch-id: the review carries over")
        self.assertEqual(sum(1 for c in self.calls()["_log"] if c["stage"] == "verify"), 2)


if __name__ == "__main__":
    unittest.main()
