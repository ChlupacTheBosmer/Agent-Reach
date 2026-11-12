"""Fork: Bluesky people, profiles, posts and threads through OpenCLI (Bluesky's public API, no login).

Posting, replies and direct messages go through the outbox (agent-reach-outbox) with the user's approval, using the
official AT Protocol API and an app password (`agent-reach configure bluesky-handle`, `bluesky-app-password`).
"""
import re

from agent_reach.runtime.result import BAD_INPUT, ReachError

from ._opencli_site import OpenCLISiteChannel
from .base import Action, Param


def bsky_uri(target: str) -> str:
    """https://bsky.app/profile/<handle-or-did>/post/<rkey> (or an at:// URI) -> at:// URI."""
    t = str(target).strip()
    if t.startswith("at://"):
        return t
    m = re.search(r"bsky\.app/profile/([^/]+)/post/([\w]+)", t)
    if not m:
        raise ReachError(BAD_INPUT, "Expected a bsky.app post URL or an at:// URI")
    return f"at://{m.group(1)}/app.bsky.feed.post/{m.group(2)}"


class BlueskyChannel(OpenCLISiteChannel):
    name = site = "bluesky"
    description = "Bluesky people, profiles, posts and threads"
    domains = ("bsky.app", "bsky.social")
    level = "optional"
    actions = (
        Action("search", "Search people (handle, display name, bio)", (
            Param("query", "what to search for"), Param("limit", "max people", required=False, type=int, default=10)),
            (("opencli", "_search"),), live_test={"query": "science", "limit": 2}),
        Action("profile", "A profile: name, bio, follower and post counts", (Param("handle", "e.g. name.bsky.social"),),
               (("opencli", "_profile"),), live_test={"handle": "bsky.app"}),
        Action("posts", "A person's recent posts", (
            Param("handle", "e.g. name.bsky.social"), Param("limit", "max posts", required=False, type=int, default=10)),
            (("opencli", "_posts"),)),
        Action("thread", "A post with its replies", (Param("target", "bsky.app post URL or at:// URI"),),
               (("opencli", "_thread"),)),
    )

    def _search(self, *, query, limit, timeout):
        return self._opencli(["search", query, "--limit", str(limit)], timeout)[:limit]

    def _profile(self, *, handle, timeout):
        return self._opencli(["profile", handle.lstrip("@")], timeout, empty_is_error=False)[:1]

    def _posts(self, *, handle, limit, timeout):
        return self._opencli(["user", handle.lstrip("@"), "--limit", str(limit)], timeout)[:limit]

    def _thread(self, *, target, timeout):
        return self._opencli(["thread", bsky_uri(target)], timeout)
