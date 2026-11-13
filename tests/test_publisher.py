"""Fork: the publisher service (agent_reach/publisher)."""
import json
import threading
import time

import pytest

from agent_reach import outbox
from agent_reach.publisher import core, job, server, store


@pytest.fixture
def home_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "in_home", lambda acc, cmd, payload, timeout=120: calls.append((acc, cmd, payload)) or {"ok": True, "result": "done"})
    return calls


@pytest.fixture
def db(tmp_path, monkeypatch, home_calls):
    monkeypatch.setenv("PUBLISHER_DATA", str(tmp_path / "pub"))
    monkeypatch.setattr(core, "_probe", lambda proxy: {"country": "CZ", "timezone": "Europe/Prague"})
    conn = store.connect()
    yield conn
    conn.close()


def _bluesky(db):
    acc = core.create_account(db, {"network": "bluesky", "label": "V4AIR", "owner": "petr", "handle": "v4air.bsky.social"}, "petr")
    db.execute("UPDATE accounts SET status='ok' WHERE id=?", (acc["id"],))
    return acc


def test_signature_must_match_body_and_be_fresh():
    body, secret = b'{"a":1}', "s" * 40
    sig = server.sign(secret, body)
    assert server.verify(secret, sig, body)
    assert not server.verify(secret, sig, b'{"a":2}')
    assert not server.verify("t" * 40, sig, body)
    assert not server.verify(secret, server.sign(secret, body, int(time.time()) - 3600), body)
    assert not server.verify(secret, "", body)


def test_browser_accounts_need_one_fixed_proxy_and_never_expose_it(db, home_calls):
    with pytest.raises(core.Refused, match="proxy"):
        core.create_account(db, {"network": "twitter", "label": "V4AIR", "owner": "petr"}, "petr")
    acc = core.create_account(db, {"network": "twitter", "label": "V4AIR", "owner": "petr", "handle": "v4air",
                                   "proxy": "http://user:secret@1.2.3.4:8000"}, "petr")
    assert acc["id"] == "twitter-v4air" and acc["proxy"] == "http://1.2.3.4:8000" and "secret" not in json.dumps(acc)
    assert home_calls[-1] == ("twitter-v4air", "configure", {"proxy": "http://user:secret@1.2.3.4:8000",
                                                             "browser_locale": "cs-CZ", "browser_timezone": "Europe/Prague"})


def test_preview_derives_own_target_and_hash_is_stable(db):
    acc = _bluesky(db)
    body = {"account": acc["id"], "kind": "bluesky-post", "text": "Open call is live https://v4air.eu/apply",
            "media": [{"url": "https://v4air-social.v4air.workers.dev/media/a.png", "alt": "poster"}]}
    p1, p2 = core.preview(db, body), core.preview(db, body)
    assert p1["hash"] == p2["hash"] and p1["post"]["target"] == "https://bsky.app/profile/v4air.bsky.social"
    assert p1["post"]["expected_name"] == "v4air.bsky.social" and not p1["warnings"]
    assert core.preview(db, {**body, "text": body["text"] + "!"})["hash"] != p1["hash"]


@pytest.mark.parametrize("change,msg", [({"text": "x" * 301}, "at most 300"), ({"media": [{"url": "https://x/1"}] * 5}, "at most 4"),
                                        ({"media": [{"url": "http://x/1"}]}, "https"), ({"text": " "}, "no text"),
                                        ({"kind": "facebook-group-post"}, "supports")])
def test_preview_refuses_what_the_network_cannot_take(db, change, msg):
    acc = _bluesky(db)
    with pytest.raises(core.Refused, match=msg):
        core.preview(db, {"account": acc["id"], "kind": "bluesky-post", "text": "hi", **change})


def test_only_the_confirmed_content_is_queued(db):
    acc = _bluesky(db)
    body = {"account": acc["id"], "kind": "bluesky-post", "text": "Hello"}
    h = core.preview(db, body)["hash"]
    with pytest.raises(core.Refused, match="not confirmed"):
        core.enqueue(db, {**body, "text": "Hello!", "confirm": h, "author": "petr"}, "petr")
    with pytest.raises(core.Refused, match="author"):
        core.enqueue(db, {**body, "confirm": h}, "petr")
    post = core.enqueue(db, {**body, "confirm": h, "author": "petr"}, "petr")
    assert post["status"] == "queued" and post["author"] == "petr" and post["hash"] == h


def test_queue_runs_due_posts_once_and_never_retries_failures(db, monkeypatch):
    acc = _bluesky(db)
    body = {"account": acc["id"], "kind": "bluesky-post", "text": "Later"}
    h = core.preview(db, body)["hash"]
    later = core.enqueue(db, {**body, "confirm": h, "author": "petr", "at": "2099-01-01T10:00:00Z"}, "petr")
    now = core.enqueue(db, {**body, "confirm": h, "author": "petr"}, "petr")
    monkeypatch.setattr(core, "in_home", lambda *a, **k: {"ok": False, "error": "Bluesky: HTTP 401"})
    done = core.run_due(db)
    assert done["id"] == now["id"] and done["status"] == "failed" and "401" in done["result"]
    assert core.run_due(db) is None  # the failed post is not picked again, the future one is not due
    assert core.get_post(db, later["id"])["status"] == "queued"


def test_job_publishes_bluesky_images_through_the_official_api(tmp_path, monkeypatch):
    from agent_reach.config import Config
    Config().set("bluesky_handle", "v4air.bsky.social")
    Config().set("bluesky_app_password", "test-app-password")
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(job, "_download", lambda media, folder: [{"path": str(img), "alt": "poster", "mime": "image/png"}])
    monkeypatch.setattr(outbox, "_bsky_session", lambda: {"accessJwt": "t", "handle": "v4air.bsky.social", "did": "did:plc:x"})
    calls = []

    def fake(method, path, body=None, token="", proxy="", base="", raw=None, mime=""):
        calls.append((path, body, mime))
        if path == "com.atproto.repo.uploadBlob":
            return {"blob": {"ref": "cid1", "mimeType": mime}}
        return {"uri": "at://did:plc:x/app.bsky.feed.post/abc"}
    monkeypatch.setattr(outbox, "_bsky", fake)
    post = {"account": "bluesky-v4air", "kind": "bluesky-post", "target": "https://bsky.app/profile/v4air.bsky.social",
            "expected_name": "v4air.bsky.social", "text": "Hi", "media": [{"url": "https://x/a.png", "alt": "poster"}]}
    out = job.publish({"post": post})
    assert out["ok"] and out["result"].endswith("/post/abc")
    record = calls[-1][1]["record"]
    assert calls[0][0] == "com.atproto.repo.uploadBlob" and calls[0][2] == "image/png"
    assert record["embed"]["images"] == [{"alt": "poster", "image": {"ref": "cid1", "mimeType": "image/png"}}]


def test_http_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISHER_DATA", str(tmp_path / "pub"))
    secret = "k" * 40
    server.Handler.secret = secret
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method, path, body=None, signed=True):
        import http.client
        raw = json.dumps(body).encode() if body is not None else b""
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request(method, path, body=raw or None,
                     headers={"X-Publisher-Actor": "petr", **({"X-Publisher-Signature": server.sign(secret, raw)} if signed else {})})
        r = conn.getresponse()
        return r.status, json.loads(r.read())
    try:
        assert call("GET", "/v1/networks", signed=False)[0] == 401
        code, nets = call("GET", "/v1/networks")
        assert code == 200 and "bluesky-post" in nets["bluesky"]["kinds"]
        code, err = call("POST", "/v1/accounts", {"network": "facebook", "label": "Petr", "owner": "petr"})
        assert code == 422 and "proxy" in err["error"]
        assert call("GET", "/healthz", signed=False) == (200, {"ok": True})
    finally:
        httpd.shutdown()


def test_accounts_that_are_not_ready_cannot_queue(db):
    acc = core.create_account(db, {"network": "bluesky", "label": "New", "owner": "petr", "handle": "n.bsky.social"}, "petr")
    body = {"account": acc["id"], "kind": "bluesky-post", "text": "Hi"}
    with pytest.raises(core.Refused, match="not ready"):
        core.enqueue(db, {**body, "confirm": core.preview(db, body)["hash"], "author": "petr"}, "petr")



# ── Facebook Page and Instagram through the browser; the team's proxy list ──

def test_browser_accounts_take_a_free_address_from_the_proxy_list(db, tmp_path, monkeypatch):
    pool = tmp_path / "proxies.txt"
    pool.write_text("1.1.1.1:5000:u1:p1\n2.2.2.2:5001:u2:p2\n")
    monkeypatch.setenv("PUBLISHER_PROXIES", str(pool))
    a = core.create_account(db, {"network": "instagram", "label": "V4AIR", "owner": "V4AIR", "handle": "v4air.eu"}, "petr")
    b = core.create_account(db, {"network": "facebookpage", "label": "V4AIR AI Meetup", "owner": "V4AIR", "handle": "v4air"}, "petr")
    assert (a["proxy"], b["proxy"]) == ("http://1.1.1.1:5000", "http://2.2.2.2:5001")
    with pytest.raises(core.Refused, match="no free working address"):
        core.create_account(db, {"network": "twitter", "label": "V4AIR", "owner": "V4AIR"}, "petr")


def test_page_and_instagram_rules(db):
    for net, label, handle in [("facebookpage", "V4AIR AI Meetup", "v4air"), ("instagram", "V4AIR", "v4air.eu")]:
        acc = core.create_account(db, {"network": net, "label": label, "owner": "V4AIR", "handle": handle,
                                       "proxy": f"http://u:p@{net}.example:8000"}, "petr")
        db.execute("UPDATE accounts SET status='ok' WHERE id=?", (acc["id"],))
    page = core.preview(db, {"account": "facebookpage-v4air-ai-meetup", "kind": "facebook-page-post", "text": "Hi"})["post"]
    assert page["target"] == "https://www.facebook.com/v4air" and page["expected_name"] == "V4AIR AI Meetup"
    ig = {"account": "instagram-v4air", "text": "Hi", "media": [{"url": "https://app/media/photos/a.jpg"}]}
    with pytest.raises(core.Refused, match="MP4"):
        core.preview(db, {**ig, "kind": "instagram-reel"})
    with pytest.raises(core.Refused, match="at least 1"):
        core.preview(db, {**ig, "kind": "instagram-post", "media": []})
    assert core.preview(db, {**ig, "kind": "instagram-story", "text": ""})["post"]["text"] == ""


def test_instagram_refuses_another_logged_in_account(monkeypatch):
    class FakeTab:
        def __init__(self, url, site): pass
        def wait(self, expression, seconds=25): return True
        def js(self, expression): return "/someone.else/"
        def close(self): pass
    monkeypatch.setattr(outbox, "Tab", FakeTab)
    d = {"kind": "instagram-post", "expected_name": "v4air.eu", "text": "Hi", "media": [{"path": "/tmp/a.jpg"}]}
    with pytest.raises(SystemExit, match="not logged in to Instagram as @v4air.eu"):
        outbox.instagram(d, True)
    monkeypatch.setattr(FakeTab, "js", lambda self, e: "/v4air.eu/")
    assert outbox.instagram(d, False).startswith("check ok")


def test_proxies_that_fail_or_exit_abroad_are_skipped(db, tmp_path, monkeypatch):
    pool = tmp_path / "proxies.txt"
    pool.write_text("1.1.1.1:5000:u1:p1\r\n2.2.2.2:5001:u2:p2\r\n3.3.3.3:5002:u3:p3\r\n")  # Webshare sends CRLF

    def probe(proxy):
        if "1.1.1.1" in proxy:
            raise OSError("Tunnel connection failed: 402")
        return {"country": "US" if "2.2.2.2" in proxy else "CZ", "timezone": "Europe/Prague"}
    monkeypatch.setattr(core, "_probe", probe)
    monkeypatch.setenv("PUBLISHER_PROXIES", str(pool))
    monkeypatch.setenv("PUBLISHER_PROXY_COUNTRIES", "CZ,SK")
    acc = core.create_account(db, {"network": "instagram", "label": "V4AIR", "owner": "V4AIR", "handle": "v4air.eu"}, "petr")
    assert acc["proxy"] == "http://3.3.3.3:5002"
    with pytest.raises(core.Refused, match="exits in US"):
        core.create_account(db, {"network": "twitter", "label": "X", "owner": "V4AIR", "proxy": "http://u:p@2.2.2.2:5001"}, "petr")
