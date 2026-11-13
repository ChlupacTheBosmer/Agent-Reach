"""Publisher logic: networks, accounts and their homes, preview + hash, confirmed queue, running a post."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

from . import store

#: What each network can do through the publisher. `target`: own = the account itself, else what the user picks.
NETWORKS = {
    "bluesky": {"route": "api", "kinds": {
        "bluesky-post": {"text": 300, "images": 4, "target": "own"},
        "bluesky-reply": {"text": 300, "images": 4, "target": "post"},
        "bluesky-message": {"text": 1000, "images": 0, "target": "person"}}},
    "twitter": {"route": "browser", "kinds": {
        "twitter-post": {"text": 280, "images": 4, "target": "own"},
        "twitter-reply": {"text": 280, "images": 1, "target": "post"}}},
    "facebook": {"route": "browser", "kinds": {
        "facebook-group-post": {"text": 5000, "images": 0, "target": "group"},
        "facebook-message": {"text": 2000, "images": 0, "target": "person"}}},
    "facebookpage": {"route": "browser", "kinds": {
        "facebook-page-post": {"text": 5000, "images": 10, "target": "own", "media": "image"}}},
    "instagram": {"route": "browser", "kinds": {
        "instagram-post": {"text": 2200, "images": 10, "min_media": 1, "target": "own", "media": "image or video"},
        "instagram-reel": {"text": 2200, "images": 1, "min_media": 1, "target": "own", "media": "video"}}},
    "linkedin": {"route": "browser", "kinds": {
        "linkedin-message": {"text": 2000, "images": 0, "target": "thread"},
        "linkedin-connect": {"text": 300, "images": 0, "target": "person"}}},
}
OWN_URL = {"bluesky": "https://bsky.app/profile/{h}", "twitter": "https://x.com/{h}",
           "facebookpage": "https://www.facebook.com/{h}", "instagram": "https://www.instagram.com/{h}/"}
#: Account-home settings each network accepts (written to that home's Agent Reach config, never returned).
CREDENTIALS = {"bluesky": ("bluesky_handle", "bluesky_app_password")}


class Refused(Exception):
    """A request the publisher will not carry out; the message says why."""


# ── accounts ──

def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def home(account_id: str):
    return store.data_dir() / "accounts" / account_id


LOCALES = {"CZ": "cs-CZ", "SK": "sk-SK", "PL": "pl-PL", "HU": "hu-HU", "AT": "de-AT", "DE": "de-DE"}


def _probe(proxy: str) -> dict:
    """Ask a what-is-my-IP service through the proxy: it must answer, and it tells the exit country and timezone."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    with opener.open(urllib.request.Request("https://ipinfo.io/json", headers={"User-Agent": "curl/8"}), timeout=25) as r:
        info = json.loads(r.read())
    return {"country": info.get("country", ""), "timezone": info.get("timezone", "")}


def _proxy_setup(proxy: str) -> dict:
    """Probe a proxy; refuse it if it does not work or exits outside PUBLISHER_PROXY_COUNTRIES (e.g. CZ,SK,PL,HU)."""
    try:
        info = _probe(proxy)
    except Exception as exc:  # noqa: BLE001 - any failure means: not usable
        raise Refused(f"the proxy {masked(proxy)} does not work ({type(exc).__name__}: {str(exc)[:80]})") from None
    allowed = [c.strip().upper() for c in os.environ.get("PUBLISHER_PROXY_COUNTRIES", "").split(",") if c.strip()]
    if allowed and info["country"] not in allowed:
        raise Refused(f"the proxy {masked(proxy)} exits in {info['country'] or 'an unknown country'}, "
                      f"not in {', '.join(allowed)}")
    return {"proxy": proxy, "browser_locale": LOCALES.get(info["country"], "en-US"), "browser_timezone": info["timezone"],
            "country": info["country"]}


def _pool_proxy(db) -> dict:
    """The first address in the team's proxy list (PUBLISHER_PROXIES, lines ip:port:user:password) that no account
    uses yet and that works. One address per account, kept for good, so each account always appears from the same
    household; broken or foreign addresses are skipped."""
    path = os.environ.get("PUBLISHER_PROXIES", "")
    if not path or not os.path.isfile(path):
        raise Refused("enter a proxy for this account (the publisher has no proxy list)")
    used = {masked(r[0]) for r in db.execute("SELECT proxy FROM accounts WHERE proxy != ''")}
    skipped = []
    for line in open(path, encoding="utf-8"):
        parts = line.strip().split(":")
        if len(parts) != 4:
            continue
        proxy = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
        if masked(proxy) in used:
            continue
        try:
            return _proxy_setup(proxy)
        except Refused as exc:
            skipped.append(str(exc))
    raise Refused("no free working address in the proxy list" + (f" ({'; '.join(skipped)[:400]})" if skipped else ""))


def _check_proxy(proxy: str) -> None:
    u = urlsplit(proxy)
    if u.scheme not in ("http", "https", "socks5") or not u.hostname or not u.port:
        raise Refused("proxy must look like http://user:pass@host:port (one fixed address for this account)")


def masked(proxy: str) -> str:
    u = urlsplit(proxy) if proxy else None
    return f"{u.scheme}://{u.hostname}:{u.port}" if u and u.hostname else ""


def public(acc: dict) -> dict:
    return {**acc, "proxy": masked(acc["proxy"])}


def create_account(db, body: dict, actor: str) -> dict:
    network = body.get("network")
    if network not in NETWORKS:
        raise Refused(f"unknown network {network!r}; one of {', '.join(NETWORKS)}")
    label, owner = (body.get("label") or "").strip(), (body.get("owner") or "").strip()
    if not label or not owner:
        raise Refused("label and owner are required")
    route = NETWORKS[network]["route"]
    proxy = (body.get("proxy") or "").strip()
    setup = {}
    if route == "browser":
        if proxy:
            _check_proxy(proxy)
            setup = _proxy_setup(proxy)
        else:
            setup = _pool_proxy(db)
        proxy = setup["proxy"]  # browser accounts never go out from the server's own address
    acc = {"id": f"{network}-{slug(label)}", "network": network, "label": label, "owner": owner, "route": route,
           "handle": (body.get("handle") or "").strip().lstrip("@"), "proxy": proxy,
           "status": "needs-login" if route == "browser" else "needs-credentials", "created": time.time()}
    if db.execute("SELECT 1 FROM accounts WHERE id=?", (acc["id"],)).fetchone():
        raise Refused(f"account {acc['id']} exists")
    db.execute("INSERT INTO accounts VALUES (:id,:network,:label,:owner,:route,:handle,:proxy,:status,:created)", acc)
    if setup:
        in_home(acc["id"], "configure", {k: v for k, v in setup.items() if k != "country"})
    store.log(db, actor, "account.create", {**public(acc), "proxy_country": setup.get("country", "")})
    return public(acc)


def account(db, account_id: str) -> dict:
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        raise Refused(f"no account {account_id!r}")
    return dict(row)


def set_credentials(db, account_id: str, body: dict, actor: str) -> dict:
    acc = account(db, account_id)
    allowed = CREDENTIALS.get(acc["network"], ())
    values = {k: str(v) for k, v in body.items() if k in allowed and v}
    if not values:
        raise Refused(f"{acc['network']} takes credentials {', '.join(allowed) or '(none: log in in the live view)'}")
    in_home(account_id, "configure", values)
    if values.get("bluesky_handle"):
        db.execute("UPDATE accounts SET handle=? WHERE id=?", (values["bluesky_handle"].lstrip("@"), account_id))
    db.execute("UPDATE accounts SET status='unchecked' WHERE id=?", (account_id,))
    store.log(db, actor, "account.credentials", {"account": account_id, "keys": sorted(values)})
    return public(account(db, account_id))


def groups(db, account_id: str, actor: str) -> list:
    """The Facebook groups this account is a member of, for the composer's group dropdown (one paced read)."""
    acc = account(db, account_id)
    if acc["network"] != "facebook":
        raise Refused("only Facebook accounts have groups")
    out = in_home(account_id, "groups", {})
    if not out.get("ok"):
        raise Refused(out.get("error") or "could not read the groups")
    store.log(db, actor, "account.groups", {"account": account_id, "count": len(out["result"])})
    return out["result"]


def check_account(db, account_id: str, actor: str) -> dict:
    out = in_home(account_id, "check", {})
    status = "ok" if out.get("ok") else ("needs-login" if account(db, account_id)["route"] == "browser" else "needs-credentials")
    db.execute("UPDATE accounts SET status=? WHERE id=?", (status, account_id))
    store.log(db, actor, "account.check", {"account": account_id, **out})
    return out


# ── preview, confirm, queue ──

def normalize(db, body: dict) -> dict:
    acc = account(db, body.get("account", ""))
    net = NETWORKS[acc["network"]]
    kind = body.get("kind")
    if kind not in net["kinds"]:
        raise Refused(f"{acc['network']} supports {', '.join(net['kinds'])}")
    spec = net["kinds"][kind]
    text = (body.get("text") or "").strip()
    if not text and spec["text"]:
        raise Refused("the post has no text")
    if len(text) > spec["text"]:
        raise Refused(f"{kind}: {len(text)} characters, at most {spec['text']}")
    media = [{"url": m["url"], "alt": (m.get("alt") or "").strip()} for m in body.get("media") or []]
    if len(media) > spec["images"]:
        raise Refused(f"{kind} takes at most {spec['images']} media files")
    if len(media) < spec.get("min_media", 0):
        raise Refused(f"{kind} needs at least {spec['min_media']} media file(s)")
    video = [m for m in media if m["url"].lower().split("?")[0].endswith(".mp4")]
    if spec.get("media") == "video" and len(video) != len(media):
        raise Refused(f"{kind} needs an MP4 video")
    if spec.get("media", "image") == "image" and video:
        raise Refused(f"{kind} takes images only")
    if acc["network"] == "instagram":
        for i, m in enumerate(body.get("media") or []):
            w, h = m.get("width"), m.get("height")
            if w and h and not 0.8 <= w / h <= 1.91:
                raise Refused(f"image {i + 1} is {w}x{h}: Instagram takes 4:5 (portrait) to 1.91:1 (landscape) without cropping")
    for m in media:
        if urlsplit(m["url"]).scheme != "https":
            raise Refused("media must be https URLs")
    if spec["target"] == "own":
        if not acc["handle"]:
            raise Refused("the account has no handle yet; set its credentials or handle first")
        target = OWN_URL[acc["network"]].format(h=acc["handle"])
        expected = acc["label"] if acc["network"] == "facebookpage" else acc["handle"]  # a Page shows its name
    else:
        target, expected = (body.get("target") or "").strip(), (body.get("expected_name") or "").strip()
        if not target or not expected:
            raise Refused(f"{kind} needs a target ({spec['target']}) and the exact name the page shows")
    post = {"account": acc["id"], "kind": kind, "target": target, "expected_name": expected, "text": text,
            "media": media, "options": {}}
    from agent_reach import outbox
    outbox.validate({**post, "status": "", "created": "", "result": ""})
    return post


def digest(post: dict) -> str:
    return hashlib.sha256(json.dumps(post, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def preview(db, body: dict) -> dict:
    post = normalize(db, body)
    acc = account(db, post["account"])
    warnings = [] if acc["status"] == "ok" else [f"account status is {acc['status']}; run Check first"]
    return {"post": post, "hash": digest(post), "warnings": warnings}


def enqueue(db, body: dict, actor: str) -> dict:
    post = normalize(db, body)
    if body.get("confirm") != digest(post):
        raise Refused("this content was not confirmed as previewed: preview it again and confirm the new version")
    author = (body.get("author") or "").strip()
    if not author:
        raise Refused("author is required")
    acc = account(db, post["account"])
    if acc["status"] in ("needs-credentials", "needs-login", "paused", "new"):
        raise Refused(f"account {acc['label']} is not ready ({acc['status']}): set it up on the accounts page and press Check")
    if acc["route"] == "browser" and not acc["proxy"]:
        raise Refused("this browser account has no proxy; it never posts from the server's own address")
    due = time.time()
    if body.get("at"):
        import datetime as dt
        due = dt.datetime.fromisoformat(str(body["at"]).replace("Z", "+00:00")).timestamp()
    cur = db.execute("INSERT INTO posts(account, payload, hash, author, due, created) VALUES (?,?,?,?,?,?)",
                     (acc["id"], json.dumps(post, ensure_ascii=False), digest(post), author, due, time.time()))
    store.log(db, actor, "post.queue", {"id": cur.lastrowid, "author": author, "account": acc["id"], "kind": post["kind"],
                                        "hash": digest(post), "due": due})
    return get_post(db, cur.lastrowid)


def get_post(db, post_id: int) -> dict:
    row = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        raise Refused(f"no post {post_id}")
    return {**dict(row), "payload": json.loads(row["payload"])}


def cancel(db, post_id: int, actor: str) -> dict:
    n = db.execute("UPDATE posts SET status='cancelled', finished=? WHERE id=? AND status='queued'", (time.time(), post_id)).rowcount
    if not n:
        raise Refused("only a queued post can be cancelled")
    store.log(db, actor, "post.cancel", {"id": post_id})
    return get_post(db, post_id)


# ── running ──

def in_home(account_id: str, command: str, payload: dict, timeout: float = 120) -> dict:
    """Run one job step in that account's own Agent Reach home (separate process = no shared state)."""
    h = home(account_id)
    h.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HOME": str(h), "PUBLISHER_ACCOUNT": account_id, "PUBLISHER_DATA": str(store.data_dir())}
    try:
        p = subprocess.run([sys.executable, "-m", "agent_reach.publisher.job", command], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"UNKNOWN: the {command} step did not finish in {timeout:.0f} s"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": (p.stderr or p.stdout).strip()[-600:] or f"exit {p.returncode}"}


def run_due(db, now=None) -> dict | None:
    """Publish the oldest due post, one at a time (human pace is enforced inside the job)."""
    now = time.time() if now is None else now
    row = db.execute("SELECT id FROM posts WHERE status='queued' AND due<=? ORDER BY due, id LIMIT 1", (now,)).fetchone()
    if not row:
        return None
    if not db.execute("UPDATE posts SET status='running' WHERE id=? AND status='queued'", (row["id"],)).rowcount:
        return None
    post = get_post(db, row["id"])
    out = in_home(post["account"], "publish", {"post": post["payload"]}, timeout=1800)  # pacing waits + upload
    err = str(out.get("error") or "")
    # never retried automatically; 'unknown' = it may be out, a person looks before anything is sent again
    status = "sent" if out.get("ok") else "unknown" if err.startswith("UNKNOWN") or "Traceback" in err else "failed"
    db.execute("UPDATE posts SET status=?, result=?, finished=? WHERE id=?",
               (status, out.get("result") or out.get("error") or "", time.time(), row["id"]))
    store.log(db, "publisher", f"post.{status}", {"id": row["id"], **out})
    return get_post(db, row["id"])
