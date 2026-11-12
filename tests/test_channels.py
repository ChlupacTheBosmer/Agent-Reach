# -*- coding: utf-8 -*-
"""Tests for channel registry basics and URL routing."""

from agent_reach.channels import get_all_channels, get_channel
from agent_reach.channels.facebook import FacebookChannel
from agent_reach.channels.instagram import InstagramChannel


class TestChannelRegistry:
    def test_get_channel_by_name(self):
        ch = get_channel("github")
        assert ch is not None
        assert ch.name == "github"

    def test_get_unknown_channel_returns_none(self):
        assert get_channel("not-exists") is None

    def test_all_channels_registered(self):
        channels = get_all_channels()
        names = [ch.name for ch in channels]
        assert "web" in names
        assert "github" in names
        assert "twitter" in names
        assert "facebook" in names
        assert "instagram" in names
        assert "reddit" in names
        assert "linkedin" in names
        for gone in ("bilibili", "xiaohongshu", "boss", "xueqiu", "xiaoyuzhou", "v2ex"):
            assert gone not in names



class TestOpenCLISiteChannels:
    def test_facebook_can_handle_common_urls(self):
        ch = FacebookChannel()
        assert ch.can_handle("https://www.facebook.com/zuck")
        assert ch.can_handle("https://m.facebook.com/groups/123")
        assert ch.can_handle("https://fb.com/some-page")
        assert ch.can_handle("https://fb.watch/abc123")
        assert not ch.can_handle("https://instagram.com/openai")

    def test_instagram_can_handle_common_urls(self):
        ch = InstagramChannel()
        assert ch.can_handle("https://www.instagram.com/openai/")
        assert ch.can_handle("https://instagram.com/p/abc123/")
        assert ch.can_handle("https://instagr.am/p/abc123/")
        assert not ch.can_handle("https://facebook.com/openai")



class TestRedditChannel:
    def test_can_handle_reddit_urls(self):
        from agent_reach.channels.reddit import RedditChannel
        ch = RedditChannel()
        assert ch.can_handle("https://www.reddit.com/r/python/comments/abc123/")
        assert ch.can_handle("https://redd.it/abc123")
        assert not ch.can_handle("https://github.com/user/repo")
        assert not ch.can_handle("https://news.ycombinator.com/item?id=123")
