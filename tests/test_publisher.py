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
    assert home_calls[-1] == ("twitter-v4air", "configure", {"proxy": "http://user:secret@1.2.3.4:8000"})


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


# ── Meta: Facebook Page and Instagram (phase 3) ──

def _meta_account(db, network, handle):
    acc = core.create_account(db, {"network": network, "label": "V4AIR", "owner": "V4AIR", "handle": handle}, "petr")
    db.execute("UPDATE accounts SET status='ok' WHERE id=?", (acc["id"],))
    return acc


@pytest.fixture
def graph(monkeypatch):
    """Fake Graph API: records calls, answers like Meta does."""
    from agent_reach.publisher import meta
    calls = []

    def fake(method, path, params=None):
        calls.append((method, path, dict(params or {})))
        if path == "oauth/access_token":
            return {"access_token": "long" if params.get("grant_type") else "short"}
        if path == "me/accounts":
            return {"data": [{"id": "P1", "name": "V4AIR", "access_token": "page-token",
                              "instagram_business_account": {"id": "IG1", "username": "v4air.eu"}},
                             {"id": "P2", "name": "Other page", "access_token": "x"}]}
        if method == "GET" and path == "P1":
            return {"name": "V4AIR"}
        if method == "GET" and path == "IG1":
            return {"username": "v4air.eu"}
        if method == "GET" and "fields" in (params or {}) and "status_code" in params["fields"]:
            return {"status_code": "FINISHED"}
        if method == "GET":
            return {"permalink": "https://www.instagram.com/p/abc/", "permalink_url": "https://facebook.com/P1/posts/9"}
        return {"id": f"id{len(calls)}"}
    monkeypatch.setattr(meta, "graph", fake)
    return calls


def test_meta_connect_picks_the_named_page_and_stores_its_token(graph, monkeypatch):
    from agent_reach.config import Config
    from agent_reach.publisher import meta
    monkeypatch.setenv("META_APP_ID", "app")
    monkeypatch.setenv("META_APP_SECRET", "secret")
    out = meta.connect("instagram", "@v4air.eu", "CODE", "https://app/api/meta/callback")
    assert out == {"ok": True, "result": "connected v4air.eu", "handle": "v4air.eu"}
    cfg = Config(read_only=True)
    assert cfg.get("meta_page_token") == "page-token" and cfg.get("meta_ig_id") == "IG1"
    with pytest.raises(SystemExit, match="you manage: V4AIR"):
        meta.connect("facebookpage", "Nope", "CODE", "https://app/cb")


def test_meta_kinds_are_validated_by_the_publisher(db):
    acc = _meta_account(db, "instagram", "v4air.eu")
    base = {"account": acc["id"], "text": "Hi", "media": [{"url": "https://app/media/photos/a.jpg"}]}
    with pytest.raises(core.Refused, match="at least 2"):
        core.preview(db, {**base, "kind": "instagram-carousel"})
    with pytest.raises(core.Refused, match="needs a video"):
        core.preview(db, {**base, "kind": "instagram-reel"})
    with pytest.raises(core.Refused, match="at most 3 collaborators"):
        core.preview(db, {**base, "kind": "instagram-post", "options": {"collaborators": "a,b,c,d"}})
    p = core.preview(db, {**base, "kind": "instagram-post", "options": {"tag_people": "@a, b", "link": "https://x"}})
    assert p["post"]["options"] == {"tag_people": ["a", "b"]}  # link is not an Instagram option, dropped
    assert p["post"]["target"] == "https://www.instagram.com/v4air.eu/"
    story = core.preview(db, {**base, "kind": "instagram-story", "text": ""})
    assert story["post"]["text"] == ""


def test_instagram_carousel_with_tags_collaborators_and_first_comment(graph):
    from agent_reach.config import Config
    for k, v in {"meta_page_token": "page-token", "meta_ig_id": "IG1", "meta_page_id": "P1"}.items():
        Config().set(k, v)
    post = {"account": "instagram-v4air", "kind": "instagram-carousel", "expected_name": "v4air.eu", "text": "Caption",
            "media": [{"url": "https://app/media/photos/a.jpg", "alt": "poster"}, {"url": "https://app/media/videos/b.mp4", "alt": ""}],
            "options": {"tag_people": ["anna"], "collaborators": ["prg.ai"], "first_comment": "#AI"}}
    out = job.publish({"post": post})
    assert out == {"ok": True, "result": "posted: https://www.instagram.com/p/abc/"}
    creates = [p for m, path, p in graph if m == "POST" and path == "IG1/media"]
    assert creates[0]["image_url"].endswith("a.jpg") and creates[0]["alt_text"] == "poster" and '"anna"' in creates[0]["user_tags"]
    assert creates[1]["media_type"] == "VIDEO" and creates[1].get("user_tags") is None
    assert creates[2]["media_type"] == "CAROUSEL" and creates[2]["collaborators"] == '["prg.ai"]'
    assert any(path.endswith("/comments") and p["message"] == "#AI" for m, path, p in graph)


def test_meta_refuses_when_the_token_belongs_to_another_page(graph):
    from agent_reach.config import Config
    for k, v in {"meta_page_token": "page-token", "meta_page_id": "P1"}.items():
        Config().set(k, v)
    post = {"account": "facebookpage-v4air", "kind": "fbpage-post", "expected_name": "Someone else", "text": "Hi", "media": [], "options": {}}
    with pytest.raises(SystemExit, match="refusing"):  # job.main turns this into {"ok": false, "error": ...}
        job.publish({"post": post})
    assert not any(m == "POST" for m, _, _ in graph)


def test_facebook_page_post_with_photos_and_link(graph):
    from agent_reach.config import Config
    for k, v in {"meta_page_token": "page-token", "meta_page_id": "P1"}.items():
        Config().set(k, v)
    post = {"account": "facebookpage-v4air", "kind": "fbpage-post", "expected_name": "V4AIR", "text": "Open call",
            "media": [{"url": "https://app/media/photos/a.jpg", "alt": ""}], "options": {"link": "https://v4air.eu"}}
    out = job.publish({"post": post})
    assert out["ok"] and out["result"] == "posted: https://facebook.com/P1/posts/9"
    feed = next(p for m, path, p in graph if path == "P1/feed")
    assert feed["link"] == "https://v4air.eu" and json.loads(feed["attached_media[0]"])["media_fbid"]
