# -*- coding: utf-8 -*-
"""Facebook search and profiles through OpenCLI in the shared Agent Reach browser."""

import re

from agent_reach.runtime.result import BAD_INPUT, NOT_FOUND, ReachError, make_item

from ._opencli_site import OpenCLISiteChannel
from .base import Action, Param

ADMINS_JS = r"""(() => {
  if (/\/login|\/checkpoint/.test(location.pathname)) return {_blocked: location.pathname};
  const people = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const m = a.href.match(/\/groups\/[^/]+\/user\/(\d+)/); const name = (a.innerText || '').trim();
    if (!m || !name || name.length > 80 || seen.has(m[1])) continue;
    seen.add(m[1]);
    const row = a.closest('[role=listitem]') || a.parentElement.parentElement;
    const role = ((row && row.innerText) || '').split('\n').map(s => s.trim()).find(s => /^(správce|moderátor|admin|moderator)/i.test(s)) || '';
    people.push({name, id: m[1], href: a.href.split('?')[0], role});
  }
  return {group: document.title, people};
})()"""


class FacebookChannel(OpenCLISiteChannel):
    name = "facebook"
    description = "Facebook groups and pages (search), profiles and group admins"
    site = "facebook"
    domains = ("facebook.com", "fb.com", "fb.watch")
    usage = "opencli facebook search/profile/feed/groups -f yaml"
    login_hint = "facebook.com"

    level = "experimental"
    login_url = "https://www.facebook.com/login"
    actions = (
        Action(
            "search",
            "Search Facebook",
            (
                Param("query", "what to search for"),
                Param("limit", "max results", required=False, type=int, default=5),
            ),
            (("opencli", "_opencli_search"),),
            live_test={"query": "openai", "limit": 2},
            public_web=("facebook.com/",),
        ),
        Action(
            "profile",
            "A user's or page's profile",
            (Param("username", "username or page name"),),
            (("opencli", "_opencli_profile"),),
            live_test={"username": "zuck"},
        ),
        Action(
            "admins",
            "A group's admins and moderators: name, profile link, user id (for an approved message via the outbox)",
            (Param("target", "group URL"),),
            (("page", "_page_admins"),),
            timeout=180,
        ),
    )

    def _page_admins(self, *, target: str, timeout: float) -> list:  # fork: one paced page view
        from agent_reach.runtime import page
        m = re.search(r"facebook\.com/groups/([^/?#]+)", str(target))
        if not m:
            raise ReachError(BAD_INPUT, "Expected a facebook.com/groups/<group> URL")
        d = page.read("facebook", f"https://www.facebook.com/groups/{m.group(1)}/members/admins", ADMINS_JS,
                      settle=4, scroll_steps=2)
        if not d.get("people"):
            raise ReachError(NOT_FOUND, "no admins listed (the group may hide its members)")
        return [make_item(title=p["name"], url=f"https://www.facebook.com/profile.php?id={p['id']}", author=p["name"],
                          text=p.get("role") or "", raw={"user_id": p["id"], "group_user_url": p["href"], "group": d.get("group")})
                for p in d["people"]]

    def _opencli_search(self, *, query: str, limit: int, timeout: float) -> list:
        return self._opencli(["search", query, "--limit", str(limit)], timeout)[:limit]

    def _opencli_profile(self, *, username: str, timeout: float) -> list:
        return self._opencli(["profile", username], timeout, empty_is_error=False)[:1]
