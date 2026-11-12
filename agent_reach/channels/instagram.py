# -*- coding: utf-8 -*-
"""Instagram creators, profiles and posts in the shared Agent Reach browser.

Fork: Instagram refuses OpenCLI's lookup endpoints for this account, so profiles, posts and search first read the
pages a person would open (profile page, post page, keyword search page); OpenCLI stays as the fallback. Every page
load is paced by runtime/pacing.py.
"""
import re

from agent_reach.runtime.result import BAD_INPUT, NOT_FOUND, ReachError, make_item

from ._opencli_site import OpenCLISiteChannel
from .base import Action, Param

_BLOCK = "if (/\\/accounts\\/login|\\/challenge\\//.test(location.pathname)) return {_blocked: location.pathname};"
_META = "const meta = n => (document.querySelector(`meta[property=\"${n}\"],meta[name=\"${n}\"]`) || {}).content || '';"
_LINKS = ("[...new Set([...document.querySelectorAll('a[href*=\"/p/\"],a[href*=\"/reel/\"]')]"
          ".map(a => a.href.split('?')[0]))]")
PROFILE_JS = f"""(() => {{ {_BLOCK} {_META}
  const header = document.querySelector('header');
  if (!header) return {{_missing: 'Instagram profile not found'}};
  return {{url: location.href, description: meta('description'), header: header.innerText, posts: {_LINKS}}};
}})()"""
POST_JS = f"""(() => {{ {_BLOCK} {_META}
  const og = meta('og:description') || meta('description');
  if (!og) return {{_missing: 'Instagram post not found'}};
  const t = document.querySelector('time[datetime]');
  return {{url: location.href.split('?')[0], og, time: t ? t.getAttribute('datetime') : null}};
}})()"""
SEARCH_JS = f"(() => {{ {_BLOCK} return {{posts: {_LINKS}}}; }})()"

_NUM = r"([\d][\d.,\s]*[KMkm]?)"


def _profile_item(d: dict, username: str):
    desc = d.get("description") or ""
    parts = re.split(r"\s[–-]\s", desc, maxsplit=1)
    head, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    counts = [c.strip() for c in re.findall(_NUM, head)][:3] + [None, None, None]
    m = re.search(r"(.*?)\s*\(@([\w.]+)\)", rest or "")
    bio = re.split(r":\s*", rest, maxsplit=1)[-1].strip().strip("„“”\"'") if rest else ""
    name = (m.group(1).strip() if m else "") or username
    text = f"{bio}\nFollowers: {counts[0]} | Following: {counts[1]} | Posts: {counts[2]}".strip()
    return make_item(title=name, url=d.get("url"), author=(m.group(2) if m else username), text=text,
                     raw={"followers": counts[0], "following": counts[1], "posts_count": counts[2],
                          "bio": bio, "header": d.get("header"), "recent_posts": d.get("posts", [])})


def _post_item(d: dict):
    og = d.get("og") or ""
    nums = [c.strip() for c in re.findall(_NUM, og.split(" - ")[0])][:2] + [None, None]
    author = re.search(r"\s-\s([\w.]+)\s", og)
    caption = re.search(r":\s*[\"“„](.*)[\"”“]\.?\s*$", og, re.S)
    return make_item(title=None, url=d.get("url"), author=author.group(1) if author else None,
                     published_at=d.get("time"), text=caption.group(1) if caption else og,
                     raw={"likes": nums[0], "comments": nums[1], "og": og})


class InstagramChannel(OpenCLISiteChannel):
    name = "instagram"
    description = "Instagram creators, profiles, posts and your saved posts"
    site = "instagram"
    domains = ("instagram.com", "instagr.am")
    usage = "agent-reach instagram profile|posts|search|read|saved"
    login_hint = "instagram.com"
    login_cookie = "sessionid"

    level = "experimental"
    login_url = "https://www.instagram.com/accounts/login/"
    actions = (
        Action(
            "search",
            "Find creators and posts for a keyword (keyword results page; each hit is a post with its creator)",
            (
                Param("query", "what to search for"),
                Param("limit", "max posts to open (each is one paced page view)", required=False, type=int, default=3),
            ),
            (("page", "_page_search"), ("opencli", "_opencli_search")),
            live_test={"query": "nasa", "limit": 1}, timeout=420,
        ),
        Action(
            "profile",
            "A creator's profile: name, bio, follower counts, links to recent posts",
            (Param("username", "Instagram username"),),
            (("page", "_page_profile"), ("opencli", "_opencli_profile")),
            live_test={"username": "nasa"}, timeout=180,
        ),
        Action(
            "posts",
            "A creator's recent posts with captions (each post is one paced page view)",
            (
                Param("username", "Instagram username"),
                Param("limit", "max posts", required=False, type=int, default=3),
            ),
            (("page", "_page_posts"), ("opencli", "_opencli_posts")),
            live_test={"username": "nasa", "limit": 1}, timeout=420,
        ),
        Action(
            "read",
            "One post or reel: creator, date, likes, comments count, full caption",
            (Param("target", "post or reel URL"),),
            (("page", "_page_read"),),
            timeout=180,
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

    # ── page views (fork) ──

    @staticmethod
    def _page(url: str, js: str):
        from agent_reach.runtime import page
        return page.read("instagram", url, js)

    def _page_profile(self, *, username: str, timeout: float) -> list:
        user = username.strip().lstrip("@").strip("/")
        if not re.fullmatch(r"[\w.]{1,30}", user):
            raise ReachError(BAD_INPUT, "Expected an Instagram username")
        return [_profile_item(self._page(f"https://www.instagram.com/{user}/", PROFILE_JS), user)]

    def _page_read(self, *, target: str, timeout: float) -> list:
        url = str(target).split("?")[0]
        if not (self.can_handle(url) and re.search(r"/(p|reel|reels|tv)/[\w-]+", url)):
            raise ReachError(BAD_INPUT, "Expected an Instagram post or reel URL")
        return [_post_item(self._page(url, POST_JS))]

    def _page_posts(self, *, username: str, limit: int, timeout: float) -> list:
        profile = self._page_profile(username=username, timeout=timeout)[0]
        links = profile["raw"]["recent_posts"][:max(0, limit)]
        return [self._page_read(target=u, timeout=timeout)[0] for u in links]

    def _page_search(self, *, query: str, limit: int, timeout: float) -> list:
        from urllib.parse import quote
        found = self._page(f"https://www.instagram.com/explore/search/keyword/?q={quote(query)}", SEARCH_JS)
        links = found.get("posts", [])[:max(0, limit)]
        if not links:
            raise ReachError(NOT_FOUND, f"no Instagram results for {query!r}")
        return [self._page_read(target=u, timeout=timeout)[0] for u in links]

    # ── OpenCLI (fallback; Instagram refuses most of its lookups for this account) ──

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
