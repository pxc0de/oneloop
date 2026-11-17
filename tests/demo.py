"""Fill a throwaway ONELOOP_HOME with realistic runs so the dashboard can be
seen and developed without spending anything:

    python3 -m tests.demo /tmp/oneloop-demo
    ONELOOP_HOME=/tmp/oneloop-demo ./bin/oneloop dashboard
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from tests import test_pipeline as T
from tests.fakes.fake_github import FakeGitHub


def main(home: str) -> None:
    work = Path(tempfile.mkdtemp())
    os.environ.update({
        "ONELOOP_HOME": home, "ONELOOP_CLAUDE_BIN": str(T.FAKE_CLAUDE), "ONELOOP_NO_NOTIFY": "1",
        "ONELOOP_CI_POLL_S": "0", "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
        "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.com",
    })
    from oneloop import pipeline, state

    def repo(name: str):
        remote = work / f"{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        path = work / name
        subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True, capture_output=True)
        (path / "app.py").write_text("def greet():\n    pass\n")
        (path / ".oneloop.toml").write_text('[checks]\ntest = "python3 -c \\"import app\\""\n')
        subprocess.run("git add -A && git commit -qm init && git push -q origin HEAD:main", shell=True, cwd=path, check=True)
        return str(path), FakeGitHub(f"acme/{name}", str(remote))

    def scenario(**stages):
        p = work / "scenario.json"
        p.write_text(json.dumps(stages))
        Path(str(p) + ".calls").unlink(missing_ok=True)
        os.environ["ONELOOP_FAKE_SCENARIO"] = str(p)

    good = {"output": T.BUILD_OK, "write": {"app.py": "def greet():\n    print('hello')\n"}}
    api_path, api = repo("api")
    web_path, web = repo("web")
    issues = [
        (api, api_path, 11, "Add rate limit headers", dict(spec=[{"output": T.SPEC_OK, "cost": 0.42}], build=[{**good, "cost": 3.1}],
            verify=[{"output": T.VERIFY_PASS, "cost": 0.9}], review=[{"output": T.REVIEW_CLEAN, "cost": 1.4}])),
        (api, api_path, 12, "Fix pagination cursor off-by-one", dict(spec=[{"output": T.SPEC_OK, "cost": 0.5}],
            build=[{"output": T.BUILD_OK, "write": {"app.py": "def greet():\n    print('helo')\n"}, "cost": 2.2}, {**good, "cost": 0.8}],
            verify=[{"output": T.VERIFY_PASS, "cost": 0.7}], review=[{"output": T.REVIEW_BLOCK, "cost": 1.6}, {"output": T.REVIEW_R2, "cost": 0.9}],
            validate=[{"output": T.VALIDATE_CONFIRM, "cost": 0.4}])),
        (web, web_path, 88, "Dark mode toggle in settings", dict(spec=[{"output": T.SPEC_OK}], build=[good],
            verify=[{"output": T.VERIFY_CANT, "cost": 0.6}])),
        (api, api_path, 13, "Rotate session signing keys", dict(spec=[{"output": T.SPEC_OK}],
            build=[{"output": T.BUILD_OK, "write": {"app.py": "def greet():\n    print('hello')\n", "REVIEW.md": "x"}, "cost": 4.0}],
            verify=[{"output": T.VERIFY_PASS}], review=[{"output": T.REVIEW_CLEAN}], brief=[{"output": T.BRIEF, "cost": 0.2}])),
        (web, web_path, 90, "Support ?next= after login", dict(spec=[{"output": {**T.SPEC_OK, "ready": False,
            "questions": ["Should ?next= allow other origins, or only paths on this site? I'd allow paths only."]}}])),
    ]
    for gh, path, n, title, sc in issues:
        gh.add_issue(n, title, f"{title}. See discussion.")
        scenario(**sc)
        s = pipeline.start(gh.repo, path, n, gh, "main")
        pipeline.drive(s, gh)
        print(f"{gh.repo}#{n}: {state.load(gh.repo, n).status}")

    # One run that is mid-flight when its process died.
    web.add_issue(91, "Debounce search input", "Typing fast fires too many requests.")
    scenario(spec=[{"output": T.SPEC_OK}], build=[good])
    s = pipeline.start(web.repo, web_path, 91, web, "main")
    for phase in ("spec", "build"):
        s = pipeline.drive(s, web, only=phase)
    state.write_json(s.dir / "live.json", {"ts": time.time() - 40, "phase": "checks", "activity": "Bash `npm test`"})
    print(f"wrote demo runs to {home}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/oneloop-demo")
