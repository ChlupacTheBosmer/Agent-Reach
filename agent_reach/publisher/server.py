"""HTTP API of the publisher (called only by the app's Worker) and its scheduler.

    PUBLISHER_SECRET=... PUBLISHER_DATA=/data agent-reach-publisher [--host 0.0.0.0] [--port 8080]

Every request is signed: X-Publisher-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>.
The member acting is named in X-Publisher-Actor (the Worker knows who is logged in). API: docs/publisher.md.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core, store

MAX_SKEW = 300
MAX_BODY = 1_000_000


def sign(secret: str, body: bytes, t: int | None = None) -> str:
    t = int(time.time()) if t is None else t
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, header: str, body: bytes, now: float | None = None) -> bool:
    m = re.fullmatch(r"t=(\d+),v1=([0-9a-f]{64})", header or "")
    if not m or abs((time.time() if now is None else now) - int(m.group(1))) > MAX_SKEW:
        return False
    return hmac.compare_digest(sign(secret, body, int(m.group(1))), header)


ROUTES = [
    ("GET", r"/v1/networks", lambda db, b, a, m: core.NETWORKS),
    ("GET", r"/v1/accounts", lambda db, b, a, m: [core.public(x) for x in store.rows(db.execute("SELECT * FROM accounts ORDER BY network, label"))]),
    ("POST", r"/v1/accounts", lambda db, b, a, m: core.create_account(db, b, a)),
    ("PUT", r"/v1/accounts/([\w-]+)/credentials", lambda db, b, a, m: core.set_credentials(db, m[1], b, a)),
    ("POST", r"/v1/accounts/([\w-]+)/check", lambda db, b, a, m: core.check_account(db, m[1], a)),
    ("POST", r"/v1/accounts/([\w-]+)/delete", lambda db, b, a, m: core.delete_account(db, m[1], a)),
    ("POST", r"/v1/accounts/([\w-]+)/groups", lambda db, b, a, m: core.groups(db, m[1], a)),
    ("POST", r"/v1/accounts/([\w-]+)/view/start", lambda db, b, a, m: core.view_start(db, m[1], a)),
    ("GET", r"/v1/accounts/([\w-]+)/view/frame", lambda db, b, a, m: core.view_frame(db, m[1])),
    ("POST", r"/v1/accounts/([\w-]+)/view/input", lambda db, b, a, m: core.view_input(db, m[1], b)),
    ("POST", r"/v1/accounts/([\w-]+)/view/stop", lambda db, b, a, m: core.view_stop(db, m[1], a)),
    ("POST", r"/v1/preview", lambda db, b, a, m: core.preview(db, b)),
    ("POST", r"/v1/posts", lambda db, b, a, m: core.enqueue(db, b, a)),
    ("GET", r"/v1/posts/(\d+)", lambda db, b, a, m: core.get_post(db, int(m[1]))),
    ("POST", r"/v1/posts/(\d+)/cancel", lambda db, b, a, m: core.cancel(db, int(m[1]), a)),
    ("GET", r"/v1/posts", lambda db, b, a, m: [{**r, "payload": json.loads(r["payload"])} for r in store.rows(
        db.execute("SELECT * FROM posts ORDER BY id DESC LIMIT 200"))]),
    ("GET", r"/v1/log", lambda db, b, a, m: store.rows(db.execute("SELECT * FROM log ORDER BY id DESC LIMIT 500"))),
]


class Handler(BaseHTTPRequestHandler):
    secret = ""

    def _reply(self, code: int, data) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method: str) -> None:
        if self.path == "/healthz":
            return self._reply(200, {"ok": True})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._reply(413, {"error": "body too large"})
        body = self.rfile.read(length) if length else b""
        if not verify(self.secret, self.headers.get("X-Publisher-Signature", ""), body):
            return self._reply(401, {"error": "bad or missing signature"})
        actor = (self.headers.get("X-Publisher-Actor") or "").strip() or "unknown"
        path = self.path.split("?")[0]
        for verb, pattern, fn in ROUTES:
            m = re.fullmatch(pattern, path)
            if verb == method and m:
                try:
                    data = json.loads(body or b"{}")
                    db = store.connect()
                    try:
                        return self._reply(200, fn(db, data, actor, m))
                    finally:
                        db.close()
                except core.Refused as exc:
                    return self._reply(422, {"error": str(exc)})
                except SystemExit as exc:  # outbox validation speaks in SystemExit
                    return self._reply(422, {"error": str(exc)})
                except (ValueError, KeyError, TypeError) as exc:
                    return self._reply(400, {"error": f"bad request: {exc}"})
        return self._reply(404, {"error": "no such endpoint"})

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def do_PUT(self):  # noqa: N802
        self._handle("PUT")

    def log_message(self, fmt, *args):  # keep request bodies (credentials) out of logs
        print(f"{self.address_string()} {self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)


def scheduler(stop: threading.Event, every: float = 10.0) -> None:
    """Publish due posts one at a time; the account's pacing paces writes further."""
    while not stop.is_set():
        try:
            db = store.connect()
            try:
                while core.run_due(db):
                    pass
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001 - keep the queue alive, report the problem
            print(f"scheduler: {type(exc).__name__}: {exc}", flush=True)
        stop.wait(every)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent-reach-publisher")
    ap.add_argument("--host", default=os.environ.get("PUBLISHER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PUBLISHER_PORT", "8080")))
    a = ap.parse_args(argv)
    secret = os.environ.get("PUBLISHER_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("PUBLISHER_SECRET (at least 32 characters) is required")
    Handler.secret = secret
    store.connect().close()
    db = store.connect()
    db.execute("UPDATE posts SET status='failed', result='interrupted by a restart; not retried' WHERE status='running'")
    db.close()
    stop = threading.Event()
    threading.Thread(target=scheduler, args=(stop,), daemon=True).start()
    print(f"publisher on {a.host}:{a.port}, data {store.data_dir()}", flush=True)
    try:
        ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
