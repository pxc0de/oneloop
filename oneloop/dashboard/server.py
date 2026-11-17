"""Local dashboard: one HTML page and a small JSON API over ~/.oneloop.

Listens on 127.0.0.1 only. Every request must carry a Host header for this
address (blocks DNS rebinding), and every action needs the per-start token
embedded in the page (blocks other sites posting to it). The server only
reads state; actions call the same functions as the CLI and continue the run
in a background process.
"""

from __future__ import annotations

import json
import mimetypes
import secrets
import shutil
import socketserver
import subprocess
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from oneloop import control, metrics, paths, pipeline, queue
from oneloop.github import GhCli
from oneloop.state import RunState, all_runs, liveness, load, read_events, read_json

HERE = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(24)
_health_cache: dict[str, Any] = {}


def _live(s: RunState) -> dict[str, Any]:
    return read_json(s.dir / "live.json", {}) or {}


def run_summary(s: RunState) -> dict[str, Any]:
    nh = s.needs_human
    return {
        "repo": s.repo, "issue": s.issue, "title": s.title, "status": s.status, "phase": s.phase,
        "liveness": liveness(s), "needs_human": nh.__dict__ if nh else None,
        "risk": (s.risk or {}).get("level"), "cost_usd": s.total_cost, "costs": s.costs,
        "pr_url": s.pr_url, "pr_number": s.pr_number, "rounds": s.rounds,
        "live": _live(s), "created_at": s.created_at, "updated_at": s.updated_at,
        "human_touches": s.human_touches,
        "stages": [st.__dict__ for st in s.stages],
    }


def run_detail(s: RunState) -> dict[str, Any]:
    arts: dict[str, Any] = {}
    adir = s.dir / "artifacts"
    if adir.exists():
        for f in sorted(adir.iterdir()):
            if f.suffix == ".json":
                arts[f.name] = read_json(f)
            elif f.suffix == ".md":
                arts[f.name] = f.read_text()
    sessions = sorted(p.name for p in (s.dir / "sessions").glob("*.jsonl")) if (s.dir / "sessions").exists() else []
    events = [e for e in read_events(s.dir)]
    for e in events:
        e.pop("traceback", None)
    return {**run_summary(s), "state": s.to_json(), "artifacts": arts, "sessions": sessions,
            "events": events[-500:], "worktree": s.worktree, "branch": s.branch}


def session_view(s: RunState, name: str) -> list[dict[str, Any]]:
    p = (s.dir / "sessions" / name).resolve()
    if p.parent != (s.dir / "sessions").resolve() or not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = m.get("type")
        if t == "assistant":
            for b in (m.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text"):
                    out.append({"kind": "text", "text": b["text"][:4000]})
                elif b.get("type") == "tool_use":
                    out.append({"kind": "tool", "name": b.get("name"),
                                "input": json.dumps(b.get("input"), indent=1)[:2000]})
        elif t == "user":
            for b in (m.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    text = c if isinstance(c, str) else json.dumps(c)[:2000]
                    out.append({"kind": "result", "text": (text or "")[:2000]})
        elif t == "result":
            out.append({"kind": "end", "text": f"{m.get('subtype')} · ${m.get('total_cost_usd', 0):.2f} · "
                                               f"{m.get('num_turns')} turns · {m.get('duration_ms', 0) / 1000:.0f}s"})
    return out[-400:]


def health() -> dict[str, Any]:
    if _health_cache.get("ts", 0) > time.time() - 60:
        return _health_cache["data"]

    def cmd(*args: str) -> tuple[bool, str]:
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
        lines = (p.stdout or p.stderr).strip().splitlines()
        return p.returncode == 0, lines[0] if lines else ""

    gh_ok, gh_msg = cmd("gh", "auth", "status")
    cl_ok, cl_ver = cmd("claude", "--version")
    wt_root = paths.home() / "worktrees"
    size = 0
    if wt_root.exists():
        p = subprocess.run(["du", "-sk", str(wt_root)], capture_output=True, text=True)
        size = int(p.stdout.split()[0]) * 1024 if p.returncode == 0 and p.stdout else 0
    repos = []
    from oneloop import gitops, rules
    for repo, path in control.repos().items():
        base_ref = None
        for ref in ("origin/main", "origin/master"):
            if gitops.ok(path, "rev-parse", "--verify", ref):
                base_ref = ref
                break
        has = {}
        if base_ref:
            has = {
                "verify_skill": bool(gitops.ls_tree(path, base_ref, rules.VERIFY_SKILL_DIR)),
                "review_md": gitops.show(path, base_ref, rules.REVIEW_PATH) is not None,
                "checklist": gitops.show(path, base_ref, rules.CHECKLIST_PATH) is not None,
                "config": gitops.show(path, base_ref, rules.CONFIG_PATH) is not None,
            }
        repos.append({"repo": repo, "path": path, "base": base_ref, "has": has,
                      "auto_merge": control.read()["auto_merge"].get(repo, True)})
    data = {
        "gh": {"ok": gh_ok, "detail": gh_msg}, "claude": {"ok": cl_ok, "detail": cl_ver},
        "disk": {"worktrees_bytes": size, "free_bytes": shutil.disk_usage(str(paths.home().parent)).free},
        "repos": repos, "home": str(paths.home()),
    }
    _health_cache.update(ts=time.time(), data=data)
    return data


def _signature() -> str:
    parts = []
    root = paths.runs_root()
    if root.exists():
        for f in root.glob("*/*/*.json*"):
            if f.name in ("state.json", "live.json", "events.jsonl"):
                try:
                    parts.append(f"{f}:{f.stat().st_mtime_ns}")
                except FileNotFoundError:
                    pass
    for f in (paths.control_path(), paths.ledger_path()):
        if f.exists():
            parts.append(f"{f}:{f.stat().st_mtime_ns}")
    return str(hash(tuple(sorted(parts))))


def do_action(body: dict[str, Any]) -> dict[str, Any]:
    action = body.get("action")
    if action == "pause":
        control.set_paused(True)
        return {"ok": True}
    if action == "unpause":
        control.set_paused(False)
        return {"ok": True}
    if action == "automerge":
        control.set_auto_merge(body["repo"], bool(body.get("enabled")))
        _health_cache.clear()
        return {"ok": True}
    s = load(body["repo"], int(body["issue"]))
    if s is None:
        return {"ok": False, "error": "no such run"}
    gh = GhCli(s.repo)
    fns = {
        "approve": lambda: pipeline.approve(s, gh),
        "sendback": lambda: pipeline.sendback(s, gh, (body.get("note") or "").strip() or "Please revise."),
        "resume": lambda: pipeline.resume(s, gh),
        "cancel": lambda: pipeline.cancel(s, gh),
    }
    if action not in fns:
        return {"ok": False, "error": f"unknown action {action}"}
    try:
        fns[action]()
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if action != "cancel":
        queue.spawn(["resume", str(s.issue), "--repo", s.repo], f"{paths.repo_key(s.repo)}-{s.issue}.log")
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "oneloop"
    allowed_hosts: set[str] = set()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, default=str).encode(), "application/json")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.allowed_hosts

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"bad host", "text/plain")
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                html = (HERE / "index.html").read_text().replace("__ONELOOP_TOKEN__", TOKEN)
                return self._send(200, html.encode(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                   "script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"})
            if u.path == "/api/overview":
                return self._json({"runs": [run_summary(s) for s in all_runs()], "control": control.read(),
                                   "now": time.time()})
            if u.path == "/api/run":
                s = load(q["repo"], int(q["issue"]))
                return self._json(run_detail(s)) if s else self._json({"error": "not found"}, 404)
            if u.path == "/api/session":
                s = load(q["repo"], int(q["issue"]))
                return self._json(session_view(s, q["name"]) if s else [])
            if u.path == "/api/metrics":
                return self._json(metrics.compute(q.get("repo") or None, int(q.get("days", 30))))
            if u.path == "/api/health":
                return self._json(health())
            if u.path == "/api/evidence":
                s = load(q["repo"], int(q["issue"]))
                if not s:
                    return self._send(404, b"", "text/plain")
                p = Path(q["path"]).resolve()
                if not str(p).startswith(str((s.dir / "evidence").resolve()) + "/") or not p.is_file():
                    return self._send(404, b"", "text/plain")
                ctype = mimetypes.guess_type(p.name)[0] or "text/plain"
                if ctype.startswith("text/html") or ctype == "image/svg+xml":
                    ctype = "text/plain"  # never render agent-written markup
                return self._send(200, p.read_bytes(), ctype)
            if u.path == "/api/stream":
                return self._stream()
        except (KeyError, ValueError) as e:
            return self._json({"error": str(e)}, 400)
        self._send(404, b"not found", "text/plain")

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last, beat = "", time.time()
        try:
            while True:
                sig = _signature()
                if sig != last:
                    self.wfile.write(b"event: change\ndata: {}\n\n")
                    self.wfile.flush()
                    last = sig
                elif time.time() - beat > 15:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    beat = time.time()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or self.headers.get("X-Oneloop-Token") != TOKEN:
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in self.allowed_hosts:
            return self._send(HTTPStatus.FORBIDDEN, b"bad origin", "text/plain")
        if urlparse(self.path).path != "/api/action":
            return self._send(404, b"", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(min(length, 100_000)) or b"{}")
            return self._json(do_action(body))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # HTTPServer.server_bind does a reverse DNS lookup (getfqdn) that can
        # take half a minute on macOS; the name is never used here.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def serve(port: int = 4747, open_browser: bool = True) -> None:
    Handler.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    httpd = LocalServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"oneloop dashboard: {url}  (ctrl-c to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
