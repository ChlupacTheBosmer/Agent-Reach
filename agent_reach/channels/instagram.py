# -*- coding: utf-8 -*-
"""Instagram users, profiles and posts through OpenCLI in the shared Agent Reach browser."""

from agent_reach.runtime.result import NOT_FOUND, ReachError

from ._opencli_site import OpenCLISiteChannel
from .base import Action, Param


class InstagramChannel(OpenCLISiteChannel):
    name = "instagram"
    description = "Instagram users, profiles and a user's posts"
    site = "instagram"
    domains = ("instagram.com", "instagr.am")
    usage = "opencli instagram search/profile/user/explore -f yaml"
    login_hint = "instagram.com"

    level = "experimental"
    login_url = "https://www.instagram.com/accounts/login/"
    actions = (
        Action(
            "search",
            "Search users (not posts)",
            (
                Param("query", "what to search for"),
                Param("limit", "max results", required=False, type=int, default=5),
            ),
            (("opencli", "_opencli_search"),),
            live_test={"query": "nasa", "limit": 2},
        ),
        Action(
            "profile",
            "A user's profile",
            (Param("username", "Instagram username"),),
            (("opencli", "_opencli_profile"),),
            live_test={"username": "nasa"},
        ),
        Action(
            "posts",
            "A user's recent posts",
            (
                Param("username", "Instagram username"),
                Param("limit", "max posts", required=False, type=int, default=6),
            ),
            (("opencli", "_opencli_posts"),),
            live_test={"username": "nasa", "limit": 2},
        ),
        Action(
            "saved",
            "Your own saved posts (needs your login)",
            (
                Param("limit", "max posts", required=False, type=int, default=20),
                Param("collection", "saved collection name; omit for all saved posts", required=False),
            ),
            (("opencli", "_opencli_saved"),),
        ),
    )

    def _opencli_search(self, *, query: str, limit: int, timeout: float) -> list:
        return self._opencli(["search", query, "--limit", str(limit)], timeout)[:limit]

    def _opencli_profile(self, *, username: str, timeout: float) -> list:
        return self._opencli(["profile", username], timeout, empty_is_error=False)[:1]

    def _opencli_posts(self, *, username: str, limit: int, timeout: float) -> list:
        return self._opencli(["user", username, "--limit", str(limit)], timeout)[:limit]

    def _opencli_saved(self, *, limit: int, collection=None, timeout: float) -> list:
        args = ["saved", "--limit", str(limit)] + (["--collection", str(collection)] if collection else [])
        try:
            return self._opencli(args, timeout, empty_is_error=False)[:limit]
        except ReachError as err:
            if err.code == NOT_FOUND:  # an empty saved list is an answer, not a missing page
                return []
            raise
