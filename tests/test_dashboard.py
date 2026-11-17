import http.client
import json
import os
import tempfile
import threading
import unittest

from oneloop import control, state
from oneloop.dashboard import server


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old = os.environ.get("ONELOOP_HOME")
        os.environ["ONELOOP_HOME"] = cls.tmp.name
        s = state.RunState(repo="o/r", issue=5, repo_path="/nowhere", title="Thing")
        s.ask_human("approval", "medium risk", "### brief")
        state.save(s)
        cls.httpd = server.LocalServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        server.Handler.allowed_hosts = {f"127.0.0.1:{cls.port}"}
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        if cls.old is None:
            os.environ.pop("ONELOOP_HOME", None)
        else:
            os.environ["ONELOOP_HOME"] = cls.old
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_page_embeds_token(self):
        code, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(server.TOKEN.encode(), body)

    def test_overview_lists_runs(self):
        code, body = self.req("GET", "/api/overview")
        runs = json.loads(body)["runs"]
        self.assertEqual(runs[0]["needs_human"]["kind"], "approval")

    def test_run_detail(self):
        code, body = self.req("GET", "/api/run?repo=o/r&issue=5")
        self.assertEqual(json.loads(body)["title"], "Thing")

    def test_wrong_host_is_refused(self):
        code, _ = self.req("GET", "/api/overview", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)

    def test_action_needs_token(self):
        code, _ = self.req("POST", "/api/action", {"action": "pause"})
        self.assertEqual(code, 403)
        self.assertFalse(control.read()["paused"])
        code, body = self.req("POST", "/api/action", {"action": "pause"}, {"X-Oneloop-Token": server.TOKEN})
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertTrue(control.read()["paused"])
        self.req("POST", "/api/action", {"action": "unpause"}, {"X-Oneloop-Token": server.TOKEN})

    def test_cross_origin_post_is_refused(self):
        code, _ = self.req("POST", "/api/action", {"action": "pause"},
                           {"X-Oneloop-Token": server.TOKEN, "Origin": "https://evil.example"})
        self.assertEqual(code, 403)

    def test_evidence_outside_run_dir_is_refused(self):
        code, _ = self.req("GET", "/api/evidence?repo=o/r&issue=5&path=/etc/passwd")
        self.assertEqual(code, 404)

    def test_metrics(self):
        code, body = self.req("GET", "/api/metrics")
        self.assertEqual(code, 200)
        self.assertIn("human_touches_per_merged", json.loads(body))


if __name__ == "__main__":
    unittest.main()
