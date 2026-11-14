"""Fork: publisher inbox, metrics, drafts, saved replies, collection (agent_reach/publisher/insights.py, collect.py)."""
import json
import time

import pytest

from agent_reach import outbox
from agent_reach.publisher import collect, core, insights, store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISHER_DATA", str(tmp_path / "pub"))
    monkeypatch.setattr(core, "_probe", lambda proxy: {"country": "CZ", "timezone": "Europe/Prague", "org": "O2"})
    monkeypatch.setattr(core, "in_home", lambda *a, **k: {"ok": True, "result": "done"})
    conn = store.connect()
    acc = core.create_account(conn, {"network": "bluesky", "label": "V4AIR", "owner": "V4AIR", "handle": "v4air.bsky.social"}, "petr")
    conn.execute("UPDATE accounts SET status='ok' WHERE id=?", (acc["id"],))
    yield conn
    conn.close()


RESULT = {"inbox": [
    {"id": "bsky:1", "type": "reply", "post_url": "https://bsky.app/profile/v4air.bsky.social/post/a", "item_url": "https://bsky.app/profile/anna/post/r1",
     "author_handle": "anna.bsky.social", "author_name": "Anna", "text": "When is the deadline?", "created_at": 1000.0, "question": True},
    {"id": "bsky:2", "type": "mention", "post_url": "", "item_url": "https://bsky.app/profile/bob/post/r2",
     "author_handle": "bob.bsky.social", "author_name": "Bob", "text": "Great meetup", "created_at": 2000.0, "question": False}],
    "posts": [{"url": "https://bsky.app/profile/v4air.bsky.social/post/a", "text": "Open call", "created_at": time.time() - 3600,
               "likes": 10, "reposts": 2, "replies": 1, "quotes": 0, "saves": 1, "views": None, "impressions": None}],
    "profile": {"followers": 200, "following": 10, "posts": 5}}


def test_collection_fills_inbox_metrics_and_follower_history(db, monkeypatch):
    monkeypatch.setattr(core, "in_home", lambda acc, cmd, payload, timeout=120: {"ok": True, "result": RESULT})
    out = insights.collect_account(db, "bluesky-v4air")
    assert out["ok"] and "2 new" in out["result"]
    assert insights.collect_account(db, "bluesky-v4air")["result"].startswith("0 new")  # same items are not duplicated
    items = insights.inbox_query(db, {"question": True})
    assert [i["id"] for i in items] == ["bsky:1"] and items[0]["account_label"] == "V4AIR"
    assert [i["id"] for i in insights.inbox_query(db, {"type": ["mention"]})] == ["bsky:2"]
    assert [i["id"] for i in insights.inbox_query(db, {"search": "deadline"})] == ["bsky:1"]
    m = insights.metrics(db, {"days": 30})
    post = m["posts"][0]
    assert post["engagement"] == 14 and post["rate_basis"] == "followers" and post["engagement_rate"] == round(14 / 200, 4)
    assert m["accounts"][0]["followers"] == 200 and m["comments"]["open_questions"] == 1


def test_a_sent_reply_closes_its_inbox_item_and_the_post_is_measured(db, monkeypatch):
    monkeypatch.setattr(core, "in_home", lambda acc, cmd, payload, timeout=120: {"ok": True, "result": RESULT})
    insights.collect_account(db, "bluesky-v4air")
    body = {"account": "bluesky-v4air", "kind": "bluesky-reply", "target": "https://bsky.app/profile/anna/post/r1",
            "expected_name": "anna.bsky.social", "text": "15 January 2027"}
    p = core.preview(db, body)
    core.enqueue(db, {**body, "confirm": p["hash"], "author": "petr", "inbox": "bsky:1", "tags": ["call"]}, "petr")
    monkeypatch.setattr(core, "in_home", lambda *a, **k: {"ok": True, "result": "posted: https://bsky.app/profile/v4air.bsky.social/post/zz"})
    sent = core.run_due(db)
    assert sent["status"] == "sent" and sent["url"] == "https://bsky.app/profile/v4air.bsky.social/post/zz"
    item = [i for i in insights.inbox_query(db, {}) if i["id"] == "bsky:1"][0]
    assert item["status"] == "answered" and item["answered_by"] == "petr" and item["reply_post"] == sent["id"]
    m = insights.metrics(db, {})
    assert m["comments"]["answered"] == 1 and m["comments"]["reply_rate"] == 0.5
    assert any(x["via_app"] and x["tags"] == ["call"] for x in m["posts"])
    csv = insights.metrics_csv(db, {})["csv"]
    assert csv.splitlines()[0].startswith("created_at,network") and "Open call" in csv


def test_manual_refresh_waits_and_browser_accounts_are_read_by_day_only(db, monkeypatch):
    monkeypatch.setattr(core, "in_home", lambda acc, cmd, payload, timeout=120: {"ok": True, "result": RESULT})
    insights.collect_account(db, "bluesky-v4air", manual=True)
    with pytest.raises(core.Refused, match="a moment ago"):
        insights.collect_account(db, "bluesky-v4air", manual=True)
    night = time.mktime((2026, 11, 13, 2, 0, 0, 0, 0, -1))
    assert insights._in_window(night, "bluesky") and not insights._in_window(night, "instagram")


def test_drafts_saved_replies_and_first_comment_option(db):
    d = insights.save_draft(db, {"title": "Speaker teaser", "payload": {"text": "Soon"}}, "petr")
    insights.save_draft(db, {"id": d["id"], "title": "Speaker teaser 2", "payload": {"text": "Sooner"}}, "petr")
    assert insights.drafts(db)[0]["payload"] == {"text": "Sooner"}
    r = insights.save_reply(db, {"title": "Deadline", "text": "Apply by 15 January 2027: v4air.eu/apply"}, "petr")
    assert insights.replies(db)[0]["id"] == r["id"]
    with pytest.raises(core.Refused):
        insights.save_reply(db, {"title": "", "text": "x"}, "petr")
    p = core.preview(db, {"account": "bluesky-v4air", "kind": "bluesky-post", "text": "Hi", "options": {"first_comment": "#V4AIR", "evil": "x"}})
    assert p["post"]["options"] == {"first_comment": "#V4AIR"}


def test_bluesky_collector_reads_notifications_feed_and_profile_without_marking_seen(monkeypatch):
    calls = []
    monkeypatch.setattr(outbox, "_bsky_session", lambda: {"accessJwt": "t", "did": "did:plc:me", "handle": "v4air.bsky.social"})

    def fake(method, path, body=None, token="", **k):
        calls.append(path)
        if path.startswith("app.bsky.notification.listNotifications"):
            return {"notifications": [
                {"reason": "reply", "uri": "at://did:plc:a/app.bsky.feed.post/r1", "author": {"handle": "anna", "displayName": "Anna"},
                 "record": {"text": "Is it free?", "createdAt": "2026-11-13T10:00:00Z", "reply": {"root": {"uri": "at://did:plc:me/app.bsky.feed.post/p1"}}}},
                {"reason": "like", "uri": "at://x/app.bsky.feed.like/l", "author": {}, "record": {}}]}
        if path.startswith("app.bsky.feed.getAuthorFeed"):
            return {"feed": [{"post": {"uri": "at://did:plc:me/app.bsky.feed.post/p1", "author": {"did": "did:plc:me"},
                                       "record": {"text": "Open call", "createdAt": "2026-11-12T10:00:00Z"},
                                       "likeCount": 3, "repostCount": 1, "replyCount": 1, "quoteCount": 0}}]}
        if path.startswith("app.bsky.feed.getPostThread"):  # replies come from our post's thread, ours included
            return {"thread": {"replies": [
                {"post": {"uri": "at://did:plc:a/app.bsky.feed.post/r1", "author": {"did": "did:plc:a", "handle": "anna", "displayName": "Anna"},
                          "record": {"text": "Is it free?", "createdAt": "2026-11-13T10:00:00Z"}},
                 "replies": [{"post": {"uri": "at://did:plc:me/app.bsky.feed.post/r2", "author": {"did": "did:plc:me", "handle": "v4air.bsky.social"},
                                       "record": {"text": "Yes for partners.", "createdAt": "2026-11-13T11:00:00Z"}}}]}]}}
        return {"followersCount": 50, "followsCount": 5, "postsCount": 9}
    monkeypatch.setattr(outbox, "_bsky", fake)
    out = collect.run({"account": {"id": "bluesky-v4air", "network": "bluesky", "label": "V4AIR", "handle": "v4air.bsky.social"}})["result"]
    assert [(i["type"], i["mine"]) for i in out["inbox"]] == [("reply", False), ("reply", True)] and out["inbox"][0]["question"]
    assert out["inbox"][0]["post_url"] == "https://bsky.app/profile/v4air.bsky.social/post/p1"
    assert out["inbox"][1]["item_url"] == "https://bsky.app/profile/v4air.bsky.social/post/r2"
    assert out["posts"][0]["url"] == "https://bsky.app/profile/v4air.bsky.social/post/p1"
    assert out["posts"][0]["likes"] == 3 and out["profile"]["followers"] == 50
    assert not any("updateSeen" in c for c in calls)


def test_counts_in_page_text_are_parsed():
    assert [collect._num(v) for v in ("1,234", "1 234", "1.2K", "3 tis.", "12 komentářů", None)] == [1234, 1234, 1200, 3000, 12, None]


def test_comment_replies_are_validated_kinds():
    d = {"kind": "instagram-comment-reply", "target": "https://www.instagram.com/p/x/", "expected_name": "anna", "text": "Thanks",
         "status": "", "created": "", "result": ""}
    outbox.validate(d)
    assert outbox.SENDERS["linkedin-comment-reply"] is outbox.comment_reply
    json.dumps(core.NETWORKS)


def test_members_only_see_the_accounts_they_may_use(db, monkeypatch):
    monkeypatch.setattr(core, "in_home", lambda acc, cmd, payload, timeout=120: {"ok": True, "result": RESULT})
    insights.collect_account(db, "bluesky-v4air")
    assert insights.metrics(db, {"accounts": []})["posts"] == [] and insights.metrics(db, {"accounts": []})["comments"]["total"] == 0
    assert insights.inbox_query(db, {"account": []}) == [] and len(insights.inbox_query(db, {"id": "bsky:1"})) == 1


def test_instagram_page_parsers():
    from agent_reach.publisher.collect import ig_post_meta, ig_profile_counts
    assert ig_profile_counts("Sledující (104), Sleduji (512), Příspěvky (64) – Petr Chlup") == {"followers": 104, "following": 512, "posts": 64}
    assert ig_profile_counts("1,234 Followers, 512 Following, 64 Posts - See") == {"followers": 1234, "following": 512, "posts": 64}
    m = ig_post_meta('9 likes, 0 comments - ptrchlp dne September 23, 2024: "Romania 2024.". ')
    assert (m["likes"], m["comments"], m["caption"]) == (9, 0, "Romania 2024.")
    assert ig_post_meta('3 comments - ptrchlp on May 1, 2026: "Hi"')["likes"] is None


def test_stale_profile_lock_from_another_pod_is_dropped(tmp_path):
    import os
    import socket
    from agent_reach.publisher.job import _drop_stale_profile_lock
    os.symlink("old-pod-123", tmp_path / "SingletonLock")
    os.symlink("x", tmp_path / "SingletonSocket")
    _drop_stale_profile_lock(tmp_path)
    assert not os.path.lexists(tmp_path / "SingletonLock") and not os.path.lexists(tmp_path / "SingletonSocket")
    os.symlink(f"{socket.gethostname()}-1", tmp_path / "SingletonLock")
    _drop_stale_profile_lock(tmp_path)
    assert os.path.lexists(tmp_path / "SingletonLock")  # this pod's own lock stays


def test_fetch_now_reads_accounts_one_after_another(monkeypatch):
    import time as _t
    from agent_reach.publisher import insights, store
    seen = []
    monkeypatch.setattr(store, "connect", lambda: type("DB", (), {"close": lambda self: None})())
    monkeypatch.setattr(insights, "collect_account", lambda db, acc, actor, manual=False: seen.append((acc, manual)) or {"ok": True, "result": "1 new"})
    insights.fetch_now(["a", "b"], "Petr")
    for _ in range(50):
        if not insights.fetch_status()["running"]:
            break
        _t.sleep(0.02)
    st = insights.fetch_status()
    assert seen == [("a", True), ("b", True)] and [d["account"] for d in st["done"]] == ["a", "b"] and not st["running"]


def test_comment_parsers_and_pick():
    from agent_reach.publisher.collect import _pick, fb_comment, li_comment
    assert li_comment(["Petr Chlup", "Author", "Environmental Lawyer", "2h", ".", "4 impressions", "4"]) == ("Petr Chlup", ".")
    assert li_comment(["Jana", "• 2nd", "PhD", "3d (edited)", "Great", "Like", "Reply"]) == ("Jana", "Great")
    link = "https://www.facebook.com/x/posts/pf?comment_id=202&__cft__[0]=AZ&__tn__=R-R"
    c = fb_comment("Komentář od Petr Chlup (před 2 hod)", "Petr Chlup\n\xa0\n·\n2 h\n.\nOdpovědět", link)
    assert (c["who"], c["text"], c["link"], c["id"]) == ("Petr Chlup", ".", "https://www.facebook.com/x/posts/pf?comment_id=202", "fb:202")
    r = fb_comment("Odpověď od Jana na komentář od Petr (před 1 hod)", "Jana\n·\n1 h\nDíky!\nTo se mi líbí", link + "&reply_comment_id=9")
    assert (r["who"], r["text"], r["id"]) == ("Jana", "Díky!", "fb:9")
    posts = [{"url": f"u{i}", "replies": 2} for i in range(6)]
    assert [p["url"] for p in _pick(posts, {"u0": 2})] == ["u1", "u2", "u3", "u4"] and posts[5]["replies"] is None  # u5 waits


def test_own_comments_arrive_done_and_can_be_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISHER_DATA", str(tmp_path))
    from agent_reach.publisher import insights, store
    db = store.connect()
    db.execute("INSERT INTO accounts VALUES ('li','linkedin','Petr','Petr','browser','p','',  'ok', 0)")
    acc = {"id": "li", "network": "linkedin"}
    insights._save(db, acc, {"inbox": [{"id": "a", "text": "mine", "mine": True}, {"id": "b", "text": "theirs?"}], "posts": []}, 1.0)
    rows = {r["id"]: r for r in insights.inbox_query(db, {})}
    assert (rows["a"]["status"], rows["a"]["mine"], rows["b"]["status"], rows["b"]["mine"]) == ("done", 1, "new", 0)
    assert [r["id"] for r in insights.inbox_query(db, {"hide_mine": True})] == ["b"]
