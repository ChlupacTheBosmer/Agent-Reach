"""Read-only collection for the inbox and the metrics, run as a job step in the account's own home.

    python -m agent_reach.publisher.job collect     (payload: {"account": {...}, "known": {post_url: comments}})

Returns {"ok": true, "result": {"inbox": [...], "posts": [...], "profile": {...}}}. Every read is paced by the
account home's Agent Reach pacing; nothing here marks anything as read on a site, likes, follows or posts.

  inbox item: id, type (reply, mention, quote, comment), post_url (ours, when known), item_url, author_handle,
              author_name, text, created_at (unix)
  post:       url, text, created_at, likes, reposts, replies, quotes, views, impressions, saves, clicks (None = unknown)
  profile:    followers, following, posts, profile_views, impressions (None = unknown)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time

#: detail reads per poll at most (posts whose comment count changed); keeps a poll within the inbox budget
MAX_DETAIL = 3


def _ts(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _num(value) -> int | None:
    """'1,234' / '1 234' / '1.2K' / '3 tis.' / 17 -> int; None when unknown."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"(\d[\d\s.,]*)\s*(k|tis|m|mil)?\b", str(value).lower().replace("\u00a0", " "))
    if not m:
        return None
    num, unit = m.group(1).strip(), m.group(2)
    if unit:
        return int(float(num.replace(" ", "").replace(",", ".")) * (1000 if unit in ("k", "tis") else 1_000_000))
    return int(re.sub(r"\D", "", num))


def _id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


# ── Bluesky: official API ──

def bluesky(acc: dict, known: dict) -> dict:
    from agent_reach import outbox
    sess = outbox._bsky_session()
    tok, did, me = sess["accessJwt"], sess["did"], sess["handle"]
    get = lambda path: outbox._bsky("GET", path, token=tok)  # noqa: E731
    web = lambda uri: _bsky_web((uri or "").replace(f"at://{did}/", f"at://{me}/"))  # our links use the handle, like sent posts  # noqa: E731
    inbox = []
    notes = get("app.bsky.notification.listNotifications?limit=50").get("notifications", [])
    for n in notes:  # read-only: never updateSeen
        if n.get("reason") not in ("reply", "mention", "quote"):
            continue
        a, rec = n.get("author", {}), n.get("record", {})
        rkey = n["uri"].rsplit("/", 1)[-1]
        inbox.append({"id": "bsky:" + n["uri"], "type": n["reason"], "item_url": f"https://bsky.app/profile/{a.get('handle')}/post/{rkey}",
                      "post_url": web(n.get("reasonSubject") or (rec.get("reply") or {}).get("root", {}).get("uri", "")),
                      "author_handle": a.get("handle", ""), "author_name": a.get("displayName") or a.get("handle", ""),
                      "text": rec.get("text", ""), "created_at": _ts(rec.get("createdAt") or n.get("indexedAt"))})
    feed = get(f"app.bsky.feed.getAuthorFeed?actor={did}&limit=40&filter=posts_no_replies").get("feed", [])
    posts = []
    for item in feed:
        p = item.get("post", {})
        if p.get("author", {}).get("did") != did:
            continue  # reposts of others
        posts.append({"url": web(p["uri"]), "text": p.get("record", {}).get("text", ""), "created_at": _ts(p.get("record", {}).get("createdAt")),
                      "likes": p.get("likeCount"), "reposts": p.get("repostCount"), "replies": p.get("replyCount"),
                      "quotes": p.get("quoteCount"), "saves": p.get("bookmarkCount"), "views": None, "impressions": None})
    prof = get(f"app.bsky.actor.getProfile?actor={did}")
    return {"inbox": inbox, "posts": posts,
            "profile": {"followers": prof.get("followersCount"), "following": prof.get("followsCount"), "posts": prof.get("postsCount")}}


def _bsky_web(uri: str) -> str:
    m = re.match(r"at://([^/]+)/app\.bsky\.feed\.post/([\w]+)", uri or "")
    return f"https://bsky.app/profile/{m.group(1)}/post/{m.group(2)}" if m else ""


# ── X: OpenCLI readers in the account's browser ──

def twitter(acc: dict, known: dict) -> dict:
    from agent_reach.backends.opencli import opencli_call
    handle = acc["handle"]
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    inbox = []
    for n in rows(opencli_call("twitter", ["notifications", "--limit", "30"], timeout=180)):
        action = str(n.get("action", "")).lower()
        if not any(w in action for w in ("repl", "mention", "quote", "odpov", "zmín", "citov")):
            continue
        inbox.append({"id": "x:" + str(n.get("id") or _id(n.get("url"), n.get("text"))),
                      "type": "quote" if "quote" in action or "citov" in action else "mention" if "mention" in action or "zmín" in action else "reply",
                      "item_url": n.get("url", ""), "post_url": "", "author_handle": str(n.get("author", "")).lstrip("@"),
                      "author_name": str(n.get("author", "")), "text": n.get("text", ""), "created_at": None})
    posts = [{"url": t.get("url", ""), "text": t.get("text", ""), "created_at": _ts(t.get("created_at")),
              "likes": _num(t.get("likes")), "reposts": _num(t.get("retweets")), "replies": _num(t.get("replies")),
              "quotes": None, "views": _num(t.get("views")), "impressions": _num(t.get("views"))}
             for t in rows(opencli_call("twitter", ["tweets", handle, "--limit", "20"], timeout=180)) if not t.get("is_retweet")]
    prof = (rows(opencli_call("twitter", ["profile", handle], timeout=120)) or [{}])[0]
    return {"inbox": inbox, "posts": posts,
            "profile": {"followers": _num(prof.get("followers")), "following": _num(prof.get("following")), "posts": _num(prof.get("tweets"))}}


# ── Instagram: OpenCLI profile + recent posts; comments read from the post page (in the browser) ──

IG_POSTS = r"""[...new Set([...document.querySelectorAll('main a[href*="/p/"], main a[href*="/reel/"]')].map(a => a.href.split('?')[0]))].slice(0, 12)"""
IG_COMMENTS = r"""(() => {
  const out = [];
  for (const li of document.querySelectorAll('ul li, div[role=button] + div')) {
    const a = li.querySelector('a[href^="/"][role=link], h3 a, a[href^="/"]');
    const t = li.querySelector('span[dir=auto]:not(:has(a))') || li.querySelector('span[dir=auto]');
    const time = li.querySelector('time');
    if (!a || !t || !time) continue;
    const handle = a.getAttribute('href').replace(/\//g, '');
    if (!handle || handle.includes('explore')) continue;
    out.push({handle, text: t.innerText.trim(), at: time.getAttribute('datetime'), link: (li.querySelector('a[href*="/c/"]') || {}).href || ''});
  }
  return out.slice(0, 50);
})()"""


def instagram(acc: dict, known: dict) -> dict:
    from agent_reach import outbox
    from agent_reach.backends.opencli import opencli_call
    handle = acc["handle"]
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    prof = (rows(opencli_call("instagram", ["profile", handle], timeout=180)) or [{}])[0]
    recent = rows(opencli_call("instagram", ["user", handle, "--limit", "12"], timeout=180))
    tab = outbox.Tab(f"https://www.instagram.com/{handle}/", "instagram")
    try:
        tab.wait("document.querySelector('main')", 30)
        time.sleep(3)
        urls = tab.js(IG_POSTS) or []
    finally:
        tab.close()
    posts, inbox = [], []
    for i, r in enumerate(recent):  # user.js lists the grid in order; the grid links give the URLs
        url = urls[i] if i < len(urls) else ""
        posts.append({"url": url, "text": r.get("caption", ""), "created_at": _ts(r.get("date")), "likes": _num(r.get("likes")),
                      "replies": _num(r.get("comments")), "reposts": None, "quotes": None, "views": None, "impressions": None})
    changed = [p for p in posts if p["url"] and (p["replies"] or 0) > int(known.get(p["url"], 0) or 0)][:MAX_DETAIL]
    for p in changed:
        tab = outbox.Tab(p["url"], "instagram")
        try:
            tab.wait("document.querySelector('time')", 30)
            time.sleep(3)
            for c in tab.js(IG_COMMENTS) or []:
                if c["handle"].lower() == handle.lower():
                    continue
                inbox.append({"id": "ig:" + _id(p["url"], c["handle"], c["text"]), "type": "comment", "post_url": p["url"],
                              "item_url": c.get("link") or p["url"], "author_handle": c["handle"], "author_name": c["handle"],
                              "text": c["text"], "created_at": _ts(c.get("at"))})
        finally:
            tab.close()
    return {"inbox": inbox, "posts": posts,
            "profile": {"followers": _num(prof.get("followers")), "following": _num(prof.get("following")), "posts": _num(prof.get("posts"))}}


# ── LinkedIn: OpenCLI posts + analytics (profile); notifications page for comments and mentions ──

LI_NOTES = r"""[...document.querySelectorAll('main article, main .nt-card, main [data-urn]')].map(c => ({
  text: (c.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 600),
  link: ([...c.querySelectorAll('a[href*="/feed/update/"], a[href*="commentUrn"], a[href*="/posts/"]')].map(a => a.href)[0] || ''),
  who: ((c.querySelector('a[href*="/in/"] span[aria-hidden=true], strong') || {}).innerText || '').trim()
})).filter(n => n.link && /comment|komentov|reagoval na váš|mentioned|zmínil|replied|odpověděl/i.test(n.text)).slice(0, 30)"""


def linkedin(acc: dict, known: dict, page: bool = False) -> dict:
    from agent_reach import outbox
    from agent_reach.backends.opencli import opencli_call
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    base = f"https://www.linkedin.com/company/{acc['handle']}/" if page else f"https://www.linkedin.com/in/{acc['handle']}/"
    posts = [{"url": p.get("url", ""), "text": p.get("body") or p.get("raw_text", ""), "created_at": _ts(p.get("posted_at")),
              "likes": _num(p.get("reactions")), "replies": _num(p.get("comments")), "reposts": _num(p.get("reposts")),
              "quotes": None, "views": None, "impressions": _num(p.get("impressions"))}
             for p in rows(opencli_call("linkedin", ["posts", base, "--limit", "20"], timeout=240))]
    profile = {}
    if not page:
        a = (rows(opencli_call("linkedin", ["profile-analytics", base], timeout=180)) or [{}])[0]
        profile = {"followers": _num(a.get("followers")), "profile_views": _num(a.get("profile_views")),
                   "impressions": _num(a.get("post_impressions")), "connections": _num(a.get("connections"))}
    url = f"https://www.linkedin.com/company/{acc['handle']}/admin/notifications/all/" if page else "https://www.linkedin.com/notifications/"
    tab = outbox.Tab(url, "linkedin")
    inbox = []
    try:
        tab.wait("document.querySelector('main')", 30)
        time.sleep(4)
        for n in tab.js(LI_NOTES) or []:
            t = n["text"].lower()
            inbox.append({"id": "li:" + _id(n["link"], n["text"][:120]), "type": "mention" if "mention" in t or "zmínil" in t else "comment",
                          "post_url": "", "item_url": n["link"], "author_handle": "", "author_name": n.get("who", ""),
                          "text": n["text"], "created_at": None})
    finally:
        tab.close()
    return {"inbox": inbox, "posts": posts, "profile": profile}


# ── Facebook: OpenCLI notifications (as the Page, or as the member for groups); Page header for followers ──

FB_FOLLOWERS = r"""(() => { const t = (document.querySelector('[role=main]') || document.body).innerText;
  const m = t.match(/([\d\s.,]+\s*(?:tis\.|K|M)?)\s*(sledující|followers)/i); return m ? m[1] : null; })()"""
FB_POSTS = r"""[...document.querySelectorAll('[role=main] [role=article]')].slice(0, 10).map(a => {
  const t = a.innerText || '';
  const link = ([...a.querySelectorAll('a[href*="/posts/"], a[href*="story_fbid"], a[href*="/permalink/"]')].map(x => x.href.split('?')[0])[0]) || '';
  const n = (re) => { const m = t.match(re); return m ? m[1] : null; };
  return {url: link, text: t.slice(0, 400), comments: n(/([\d.,]+\s*(?:tis\.|K)?)\s*(?:komentář|comment)/i), shares: n(/([\d.,]+)\s*(?:sdílení|share)/i),
          reactions: ((a.querySelector('[aria-label*="reakc" i], [aria-label*="reaction" i]') || {}).innerText || '').trim()};
}).filter(p => p.url)"""


def facebook(acc: dict, known: dict, page: bool = False) -> dict:
    from agent_reach import outbox
    from agent_reach.backends.opencli import opencli_call
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    inbox = []
    for n in rows(opencli_call("facebook", ["notifications", "--limit", "30"], timeout=180)):
        text, kind = str(n.get("text", "")), str(n.get("notif_type", "")).lower()
        t = text.lower()
        if not (any(w in kind for w in ("comment", "mention", "reply")) or re.search(r"komentoval|okomentoval|zmínil|odpověděl|commented|mentioned|replied", t)):
            continue
        who = re.split(r"\s(?:okomentoval|komentoval|vás zmínil|zmínil|odpověděl|commented|mentioned|replied)", text, maxsplit=1)[0]
        inbox.append({"id": "fb:" + str(n.get("notif_id") or _id(n.get("url"), text)), "type": "mention" if "zmínil" in t or "mention" in t else "comment",
                      "post_url": "", "item_url": n.get("url", ""), "author_handle": "", "author_name": who[:80], "text": text, "created_at": None})
    posts, profile = [], {}
    if page:
        tab = outbox.Tab(f"https://www.facebook.com/{acc['handle']}", "facebook")
        try:
            tab.wait("document.querySelector('[role=main]')", 30)
            time.sleep(4)
            profile = {"followers": _num(tab.js(FB_FOLLOWERS))}
            posts = [{"url": p["url"], "text": p["text"], "created_at": None, "likes": _num(p.get("reactions")),
                      "replies": _num(p.get("comments")), "reposts": _num(p.get("shares")), "quotes": None, "views": None, "impressions": None}
                     for p in tab.js(FB_POSTS) or []]
        finally:
            tab.close()
    return {"inbox": inbox, "posts": posts, "profile": profile}


COLLECTORS = {"bluesky": bluesky, "twitter": twitter, "instagram": instagram,
              "linkedin": lambda a, k: linkedin(a, k), "linkedinpage": lambda a, k: linkedin(a, k, page=True),
              "facebook": lambda a, k: facebook(a, k), "facebookpage": lambda a, k: facebook(a, k, page=True)}


def run(payload: dict) -> dict:
    acc = payload["account"]
    out = COLLECTORS[acc["network"]](acc, payload.get("known") or {})
    for item in out["inbox"]:
        item["question"] = "?" in (item.get("text") or "")
    return {"ok": True, "result": json.loads(json.dumps(out, default=str))}
