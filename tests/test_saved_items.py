"""Fork: read-only access to the user's own saved posts and bookmarks."""
from unittest.mock import Mock

from agent_reach.channels import _opencli_site, reddit, twitter
from agent_reach.channels.instagram import InstagramChannel
from agent_reach.channels.reddit import RedditChannel
from agent_reach.channels.twitter import TwitterChannel
from agent_reach.runtime.dispatch import run_action

ROWS = [{"title": "one", "url": "https://example.test/1"}, {"title": "two", "url": "https://example.test/2"}]


def test_instagram_saved_passes_limit_and_collection(monkeypatch):
    call = Mock(return_value=ROWS)
    monkeypatch.setattr(_opencli_site, "opencli_call", call)
    out = run_action(InstagramChannel(), "saved", {"limit": 1, "collection": "Recipes"})
    assert out["ok"] and len(out["items"]) == 1
    assert call.call_args.args == ("instagram", ["saved", "--limit", "1", "--collection", "Recipes"])


def test_instagram_saved_without_collection_reads_all_saved(monkeypatch):
    call = Mock(return_value=[])
    monkeypatch.setattr(_opencli_site, "opencli_call", call)
    out = run_action(InstagramChannel(), "saved", {})
    assert out["ok"] and out["items"] == []  # an empty saved list is an answer, not an error
    assert call.call_args.args == ("instagram", ["saved", "--limit", "20"])


def test_twitter_bookmarks(monkeypatch):
    call = Mock(return_value=ROWS)
    monkeypatch.setattr(twitter, "opencli_call", call)
    out = run_action(TwitterChannel(), "bookmarks", {"limit": 5})
    assert out["ok"] and len(out["items"]) == 2
    assert call.call_args.args == ("twitter", ["bookmarks", "--limit", "5"])


def test_reddit_saved_allows_an_empty_list(monkeypatch):
    call = Mock(return_value=[])
    monkeypatch.setattr(reddit, "opencli_call", call)
    out = run_action(RedditChannel(), "saved", {})
    assert out["ok"] and out["items"] == []
    assert call.call_args.args == ("reddit", ["saved", "--limit", "20"])


def test_instagram_saved_caption_becomes_text(monkeypatch):
    rows = [{"index": 1, "user": "someone", "caption": "Seven repos worth a look", "likes": 3, "type": "carousel"}]
    monkeypatch.setattr(_opencli_site, "opencli_call", Mock(return_value=rows))
    item = run_action(InstagramChannel(), "saved", {})["items"][0]
    assert item["text"] == "Seven repos worth a look" and item["author"] == "someone"
