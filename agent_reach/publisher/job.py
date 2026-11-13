"""One publisher step, run as its own process with HOME = the account's home (see core.in_home).

    python -m agent_reach.publisher.job configure|check|publish   (JSON on stdin, one JSON line on stdout)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

CONFIG_KEYS = ("proxy", "bluesky_handle", "bluesky_app_password")
MAX_MEDIA = 25 * 1024 * 1024  # the app's media store keeps at most 25 MB per file


def configure(payload: dict) -> dict:
    from agent_reach.config import Config
    cfg = Config()
    for key, value in payload.items():
        if key not in CONFIG_KEYS:
            raise SystemExit(f"not a setting: {key}")
        cfg.set(key, value)
    return {"ok": True, "result": f"saved {', '.join(sorted(payload))}"}


def check(_payload: dict) -> dict:
    network = os.environ.get("PUBLISHER_ACCOUNT", "").split("-")[0]
    if network == "bluesky":
        from agent_reach import outbox
        from agent_reach.config import Config
        cfg = Config(read_only=True)
        if not cfg.get("bluesky_handle") or not cfg.get("bluesky_app_password"):
            return {"ok": False, "error": "no app password yet: create one in Bluesky (Settings > Privacy and security > "
                                          "App passwords, tick direct messages) and enter it on the accounts page"}
        sess = outbox._bsky_session()
        return {"ok": True, "result": f"logged in as @{sess['handle']}"}
    return {"ok": False, "error": "the live check for browser accounts arrives with the browser drivers (phase 4)"}


def _download(media: list, folder: Path) -> list:
    out = []
    for i, m in enumerate(media):
        req = urllib.request.Request(m["url"], headers={"User-Agent": "v4air-publisher"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(MAX_MEDIA + 1)
            mime = r.headers.get_content_type()
        if len(data) > MAX_MEDIA:
            raise SystemExit(f"media {i + 1} is over 25 MB")
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4"}.get(mime, ".jpg")
        path = folder / f"media-{i + 1}{ext}"
        path.write_bytes(data)
        out.append({"path": str(path), "alt": m.get("alt") or "", "mime": mime})
    return out


def publish(payload: dict) -> dict:
    from agent_reach import outbox
    from agent_reach.runtime import pacing
    post = payload["post"]
    d = {**post, "status": "approved", "created": "", "result": ""}
    outbox.validate(d)
    if post["kind"].startswith("bluesky"):
        from agent_reach.config import Config
        cfg = Config(read_only=True)
        if not cfg.get("bluesky_handle") or not cfg.get("bluesky_app_password"):
            raise SystemExit("this Bluesky account has no app password yet: enter it on the accounts page")
    site = post["kind"].split("-")[0] + ":write"
    gap, extra, cap = outbox.WRITE_LIMITS[site]
    pacing.LIMITS.setdefault(site, (gap, extra, cap))
    with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmp:
        d["media"] = _download(post.get("media") or [], Path(tmp))
        pacing.before(site)  # this account's write budget: a few a day, never a burst
        return {"ok": True, "result": outbox.SENDERS[post["kind"]](d, True)}


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = json.loads(sys.stdin.read() or "{}")
    step = {"configure": configure, "check": check, "publish": publish}.get(command)
    try:
        out = step(payload) if step else {"ok": False, "error": f"unknown command {command!r}"}
    except SystemExit as exc:
        out = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - report, never crash the queue
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
