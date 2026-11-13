"""Fork: posts and messages leave only as approved drafts (agent_reach/outbox.py)."""
import pytest

from agent_reach import outbox


def _draft(tmp_path, monkeypatch, **over):
    monkeypatch.setattr(outbox, "root", lambda: tmp_path)
    d = {"kind": "facebook-group-post", "target": "https://www.facebook.com/groups/youngfreqs/",
         "expected_name": "Young Freqs", "status": "draft", "created": "now", "result": "", "text": "Hello group\nline 2"}
    d.update(over)
    (tmp_path / "x.md").write_text(outbox.render(d), "utf-8")
    return d


def test_render_parse_round_trip_and_the_hash_covers_text_and_target():
    d = {"kind": "facebook-message", "target": "https://www.facebook.com/messages/t/1/", "expected_name": "Ana",
         "status": "draft", "created": "", "result": "", "text": "Ahoj --- jak se máš?\n\nDíky"}
    back = outbox.parse(outbox.render(d))
    assert back["text"] == d["text"] and back["kind"] == d["kind"] and outbox.digest(back) == outbox.digest(d)
    assert outbox.digest({**d, "text": d["text"] + "!"}) != outbox.digest(d)
    assert outbox.digest({**d, "target": "https://www.facebook.com/messages/t/2/"}) != outbox.digest(d)


@pytest.mark.parametrize("over,msg", [
    ({"expected_name": ""}, "expected_name"), ({"target": "https://evil.example/groups/1"}, "facebook.com"),
    ({"kind": "linkedin-connect", "target": "https://www.linkedin.com/in/a/", "text": "x" * 301}, "300"),
    ({"kind": "twitter-post", "target": "https://x.com/me", "text": "x" * 281}, "too long"), ({"text": "  "}, "no text"),
    ({"kind": "spam-everyone"}, "unknown kind"),
])
def test_validation_refuses_unsafe_drafts(over, msg):
    d = {"kind": "facebook-group-post", "target": "https://www.facebook.com/groups/g/", "expected_name": "G",
         "status": "", "created": "", "result": "", "text": "hi", **over}
    with pytest.raises(SystemExit, match=msg):
        outbox.validate(d)


def test_send_needs_the_shown_hash_and_records_the_result(tmp_path, monkeypatch):
    d = _draft(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setitem(outbox.SENDERS, "facebook-group-post", lambda draft, send: calls.append(send) or "posted")
    with pytest.raises(SystemExit, match="does not match"):
        outbox.main(["send", "x", "--sha", "000000000000"])
    assert calls == []
    assert outbox.main(["send", "x", "--sha", outbox.digest(d)]) == 0
    assert calls == [True]
    saved = outbox.parse((tmp_path / "x.md").read_text("utf-8"))
    assert saved["status"] == "sent" and "posted" in saved["result"]
    with pytest.raises(SystemExit, match="this draft was sent"):
        outbox.main(["send", "x", "--sha", outbox.digest(saved)])


def test_check_is_a_dry_run(tmp_path, monkeypatch):
    _draft(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setitem(outbox.SENDERS, "facebook-group-post", lambda draft, send: calls.append(send) or "check ok")
    assert outbox.main(["check", "x"]) == 0 and calls == [False]
    assert outbox.parse((tmp_path / "x.md").read_text("utf-8"))["status"] == "draft"


def test_a_failed_send_is_recorded_not_retried(tmp_path, monkeypatch):
    d = _draft(tmp_path, monkeypatch)

    def boom(draft, send):
        raise SystemExit("the page does not show the group name")
    monkeypatch.setitem(outbox.SENDERS, "facebook-group-post", boom)
    with pytest.raises(SystemExit):
        outbox.main(["send", "x", "--sha", outbox.digest(d)])
    assert outbox.parse((tmp_path / "x.md").read_text("utf-8"))["status"] == "failed"


@pytest.mark.parametrize("target,want", [
    ("https://www.facebook.com/groups/554260651294477/user/100008345325569/", "100008345325569"),
    ("https://www.facebook.com/messages/t/ana.novak/", "ana.novak"),
    ("https://www.facebook.com/profile.php?id=123456", "123456"),
    ("https://www.facebook.com/ana.novak", "ana.novak"),
])
def test_messenger_targets(target, want):
    assert outbox._messenger_url(target) == f"https://www.facebook.com/messages/t/{want}/"


def test_twitter_reply_must_target_the_expected_author(monkeypatch):
    d = {"kind": "twitter-reply", "target": "https://x.com/someone_else/status/1", "expected_name": "@nasa", "text": "hi"}
    with pytest.raises(SystemExit, match="not a post by @nasa"):
        outbox.twitter(d, send=False)


def test_bluesky_link_facets_use_byte_offsets():
    text = "Přihlášky: https://v4air.eu/apply díky"
    facet = outbox._facets(text)[0]
    raw = text.encode("utf-8")
    assert raw[facet["index"]["byteStart"]:facet["index"]["byteEnd"]] == b"https://v4air.eu/apply"


def test_bluesky_mentions_become_tags_and_unknown_handles_stay_text():
    text = "Díky @alice.bsky.social a @nobody.example za pomoc, mail@x.cz nic"
    facets = outbox._facets(text, resolve=lambda h: "did:plc:alice" if h == "alice.bsky.social" else None)
    raw = text.encode("utf-8")
    mentions = [f for f in facets if f["features"][0]["$type"].endswith("#mention")]
    assert len(mentions) == 1 and mentions[0]["features"][0]["did"] == "did:plc:alice"
    assert raw[mentions[0]["index"]["byteStart"]:mentions[0]["index"]["byteEnd"]] == b"@alice.bsky.social"


def test_x_unconfirmed_post_is_confirmed_on_the_timeline(monkeypatch):
    """OpenCLI times out waiting for an English toast; the post is then found on the timeline and reported as posted."""
    from agent_reach import outbox
    from agent_reach.backends import opencli

    class Tab:
        def __init__(self, *a): pass
        def wait(self, *a): return True
        def js(self, *a): return "Petr @petaschlup"
        def close(self): pass

    def call(site, args, timeout):
        if args[0] == "post":
            raise RuntimeError("twitter post timed out after 15s")
        return [{"text": "Hello V4AIR", "url": "https://x.com/petaschlup/status/1"}]

    monkeypatch.setattr(outbox, "Tab", Tab)
    monkeypatch.setattr(opencli, "opencli_call", call)
    monkeypatch.setattr(outbox.time, "sleep", lambda s: None)
    d = {"kind": "twitter-post", "target": "https://x.com/petaschlup", "expected_name": "petaschlup", "text": "Hello V4AIR", "media": []}
    assert outbox.twitter(d, send=True) == "posted: https://x.com/petaschlup/status/1"
