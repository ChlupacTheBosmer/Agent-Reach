"""Read-only collection for the inbox and the metrics, run as a job step in the account's own home.

    python -m agent_reach.publisher.job collect     (payload: {"account": {...}, "known": {post_url: comments}})

Returns {"ok": true, "result": {"inbox": [...], "posts": [...], "profile": {...}}}. Every read is paced by the
account home's Agent Reach pacing; nothing here marks anything as read on a site, likes, follows or posts.

  inbox item: id, type (reply, mention, quote, comment), post_url (ours, when known), item_url, author_handle,
              author_name, text, created_at (unix), mine (written by the account itself)
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
MAX_DETAIL = 4


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


# ── comments are read under our own posts (the reliable way; notifications never show our own comments) ──
# Each inbox item carries "mine": written by the account itself. A poll opens at most MAX_DETAIL posts whose comment
# count grew; the others keep their old count, so the next poll opens them (otherwise their comments were never read).


def _pick(posts: list, known: dict) -> list:
    """The posts to open this poll (comment count grew since the last read); the rest keep their old count."""
    grew = [p for p in posts if p.get("url") and (p.get("replies") or 0) > int(known.get(p["url"], 0) or 0)]
    for p in grew[MAX_DETAIL:]:
        p["replies"] = known.get(p["url"])  # not read yet: stays "grown" until a poll opens it
    return grew[:MAX_DETAIL]


def _unread(p: dict, known: dict) -> None:
    """Opening the post failed: keep its old count so the next poll tries again."""
    p["replies"] = known.get(p["url"])


# ── Bluesky: official API (notifications for mentions and quotes, threads of our posts for every reply) ──

def bluesky(acc: dict, known: dict) -> dict:
    import urllib.parse
    from agent_reach import outbox
    sess = outbox._bsky_session()
    tok, did, me = sess["accessJwt"], sess["did"], sess["handle"]
    get = lambda path: outbox._bsky("GET", path, token=tok)  # noqa: E731
    web = lambda uri: _bsky_web((uri or "").replace(f"at://{did}/", f"at://{me}/"))  # our links use the handle, like sent posts  # noqa: E731
    inbox = []
    for n in get("app.bsky.notification.listNotifications?limit=50").get("notifications", []):  # read-only: never updateSeen
        if n.get("reason") not in ("mention", "quote"):
            continue  # replies come from the threads below (ours included)
        a, rec = n.get("author", {}), n.get("record", {})
        rkey = n["uri"].rsplit("/", 1)[-1]
        inbox.append({"id": "bsky:" + n["uri"], "type": n["reason"], "item_url": f"https://bsky.app/profile/{a.get('handle')}/post/{rkey}",
                      "post_url": web(n.get("reasonSubject") or ""), "author_handle": a.get("handle", ""),
                      "author_name": a.get("displayName") or a.get("handle", ""), "text": rec.get("text", ""),
                      "created_at": _ts(rec.get("createdAt") or n.get("indexedAt")), "mine": a.get("did") == did})
    feed = get(f"app.bsky.feed.getAuthorFeed?actor={did}&limit=40&filter=posts_no_replies").get("feed", [])
    posts, uri_of = [], {}
    for item in feed:
        p = item.get("post", {})
        if p.get("author", {}).get("did") != did:
            continue  # reposts of others
        url = web(p["uri"])
        uri_of[url] = p["uri"]
        posts.append({"url": url, "text": p.get("record", {}).get("text", ""), "created_at": _ts(p.get("record", {}).get("createdAt")),
                      "likes": p.get("likeCount"), "reposts": p.get("repostCount"), "replies": p.get("replyCount"),
                      "quotes": p.get("quoteCount"), "saves": p.get("bookmarkCount"), "views": None, "impressions": None})
    for p in _pick(posts, known):
        try:
            th = get("app.bsky.feed.getPostThread?uri=" + urllib.parse.quote(uri_of[p["url"]], safe="") + "&depth=8&parentHeight=0").get("thread", {})
        except SystemExit:
            _unread(p, known)
            continue
        stack = list(th.get("replies") or [])
        while stack:
            r = stack.pop(0)
            q = r.get("post") or {}
            stack += r.get("replies") or []
            if not q.get("uri"):
                continue
            a, rec = q.get("author", {}), q.get("record", {})
            inbox.append({"id": "bsky:" + q["uri"], "type": "reply", "post_url": p["url"],
                          "item_url": _bsky_web(q["uri"].replace(f"at://{a.get('did')}/", f"at://{a.get('handle')}/")),
                          "author_handle": a.get("handle", ""), "author_name": a.get("displayName") or a.get("handle", ""),
                          "text": rec.get("text", ""), "created_at": _ts(rec.get("createdAt")), "mine": a.get("did") == did})
    prof = get(f"app.bsky.actor.getProfile?actor={did}")
    return {"inbox": inbox, "posts": posts,
            "profile": {"followers": prof.get("followersCount"), "following": prof.get("followsCount"), "posts": prof.get("postsCount")}}


def _bsky_web(uri: str) -> str:
    m = re.match(r"at://([^/]+)/app\.bsky\.feed\.post/([\w]+)", uri or "")
    return f"https://bsky.app/profile/{m.group(1)}/post/{m.group(2)}" if m else ""


# ── X: our posts and replies from the profile page, the replies under each from its own page ──

#: every post on the page: author (from its link), text, link, time, reply/like/repost counts (from the buttons' labels)
X_ARTICLES = r"""[...document.querySelectorAll('article')].map(a => {
  const link = ((a.querySelector('a[href*="/status/"] time') || {closest: () => null}).closest('a') || {}).href || '';
  const aria = (id) => (a.querySelector(`[data-testid=${id}]`) || {getAttribute: () => ''}).getAttribute('aria-label') || '';
  return {who: ((link.match(/x\.com\/([^/]+)\/status/) || [])[1] || '').toLowerCase(), url: link.split('?')[0],
          text: (a.querySelector('[data-testid=tweetText]') || {}).innerText || '',
          at: (a.querySelector('time') || {getAttribute: () => ''}).getAttribute('datetime'),
          name: ((a.querySelector('[data-testid=User-Name]') || {}).innerText || '').split('\n')[0],
          replies: aria('reply'), likes: aria('like') || aria('unlike'), reposts: aria('retweet') || aria('unretweet'),
          views: ((a.querySelector('a[href$="/analytics"]') || {getAttribute: () => ''}).getAttribute('aria-label') || '')};
}).filter(x => x.url)"""


def twitter(acc: dict, known: dict) -> dict:
    from agent_reach import outbox
    from agent_reach.backends.opencli import opencli_call
    handle = acc["handle"].lower()
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    inbox = []
    for n in rows(opencli_call("twitter", ["notifications", "--limit", "30"], timeout=180)):
        action = str(n.get("action", "")).lower()
        if not any(w in action for w in ("mention", "quote", "zmín", "citov")):
            continue  # replies come from our posts' pages below (ours included)
        sid = (re.search(r"/status/(\d+)", n.get("url", "")) or [None, None])[1]
        inbox.append({"id": "x:" + (sid or str(n.get("id") or _id(n.get("url"), n.get("text")))),
                      "type": "quote" if "quote" in action or "citov" in action else "mention",
                      "item_url": n.get("url", ""), "post_url": "", "author_handle": str(n.get("author", "")).lstrip("@"),
                      "author_name": str(n.get("author", "")), "text": n.get("text", ""), "created_at": None,
                      "mine": str(n.get("author", "")).lstrip("@").lower() == handle})
    tab = outbox.Tab(f"https://x.com/{handle}/with_replies", "twitter")  # OpenCLI's tweets reader returns nothing here
    try:
        tab.wait("document.querySelector('article')", 30)
        time.sleep(4)
        ours = [a for a in (tab.js(X_ARTICLES) or []) if a["who"] == handle]
        posts = [{"url": a["url"], "text": a["text"], "created_at": _ts(a["at"]), "likes": _num(a["likes"]), "reposts": _num(a["reposts"]),
                  "replies": _num(a["replies"]), "quotes": None, "views": _num(a["views"]), "impressions": _num(a["views"])} for a in ours[:20]]
        for p in _pick(posts, known):
            tab.js(f"location.href = {json.dumps(p['url'])}")
            time.sleep(2)
            if not tab.wait("document.querySelectorAll('article').length > 0", 30):
                _unread(p, known)
                continue
            time.sleep(3)
            arts = tab.js(X_ARTICLES) or []
            at = next((i for i, a in enumerate(arts) if a["url"] == p["url"]), None)
            if at is None:
                _unread(p, known)
                continue
            for a in arts[at + 1:]:  # what stands below our post on its page are its replies
                sid = (re.search(r"/status/(\d+)", a["url"]) or [None, None])[1]
                inbox.append({"id": "x:" + (sid or _id(a["url"])), "type": "reply", "post_url": p["url"], "item_url": a["url"],
                              "author_handle": a["who"], "author_name": a["name"] or a["who"], "text": a["text"],
                              "created_at": _ts(a["at"]), "mine": a["who"] == handle})
    finally:
        tab.close()
    prof = (rows(opencli_call("twitter", ["profile", handle], timeout=120)) or [{}])[0]
    return {"inbox": inbox, "posts": posts,
            "profile": {"followers": _num(prof.get("followers")), "following": _num(prof.get("following")), "posts": _num(prof.get("tweets"))}}


# ── Instagram: read from the pages in the account's browser (profile header, grid, each post's description) ──
# OpenCLI's Instagram readers call Instagram's private web API, which answers 404 from the server; the pages do not.

IG_POSTS = r"""[...new Set([...document.querySelectorAll('main a[href*="/p/"], main a[href*="/reel/"]')].map(a => a.href.split('?')[0]))].slice(0, 12)"""
IG_META = r"""(document.querySelector('meta[name=description]') || {}).content || ''"""
IG_TIME = r"""(document.querySelector('main time[datetime], article time[datetime], time[datetime]') || {getAttribute: () => null}).getAttribute('datetime')"""
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
#: posts opened per poll for their numbers (each is one page view at human pace)
IG_DETAIL = 8
_COUNT = r"(\d[\d\s.,]*\s*(?:tis\.|k|m|mil\.)?)"


def ig_profile_counts(meta: str) -> dict:
    """'104 Followers, 512 Following, 64 Posts - …' or 'Sledující (104), Sleduji (512), Příspěvky (64) – …' -> counts."""
    out = {}
    for key, words in {"followers": ("followers", "sledující"), "following": ("following", "sleduji"), "posts": ("posts", "příspěvk")}.items():
        for w in words:
            m = re.search(rf"{w}\w*\s*\(\s*{_COUNT}\s*\)", meta, re.I) or re.search(rf"{_COUNT}\s+{w}", meta, re.I)
            if m:
                out[key] = _num(m.group(1))
                break
    return out


def ig_post_meta(meta: str) -> dict:
    """'9 likes, 0 comments - ptrchlp dne September 23, 2024: "caption".' (likes may be hidden) -> numbers and caption."""
    likes = re.search(rf"{_COUNT}\s*(?:likes?|to se mi líbí|označení)", meta, re.I)
    comments = re.search(rf"{_COUNT}\s*(?:comments?|komentář)", meta, re.I)
    cap = re.search(r':\s*["„“](.*)["“”]\s*\.?\s*$', meta, re.S)
    return {"likes": _num(likes.group(1)) if likes else None, "comments": _num(comments.group(1)) if comments else None,
            "caption": cap.group(1) if cap else ""}


def instagram(acc: dict, known: dict) -> dict:
    from agent_reach import outbox
    handle = acc["handle"]
    tab = outbox.Tab(f"https://www.instagram.com/{handle}/", "instagram")
    try:
        tab.wait("document.querySelector('main')", 30)
        time.sleep(3)
        profile = ig_profile_counts(tab.js(IG_META) or "")
        urls = tab.js(IG_POSTS) or []
        posts, inbox, opened = [], [], 0
        for url in urls[:IG_DETAIL]:  # one tab, page after page, like a person scrolling through their own posts
            tab.js(f"location.href = {json.dumps(url)}")
            time.sleep(2)
            tab.wait("document.querySelector('time[datetime]')", 30)
            time.sleep(2)
            m = ig_post_meta(tab.js(IG_META) or "")
            p = {"url": url, "text": m["caption"], "created_at": _ts(tab.js(IG_TIME)), "likes": m["likes"],
                 "replies": m["comments"], "reposts": None, "quotes": None, "views": None, "impressions": None}
            posts.append(p)
            if (m["comments"] or 0) > int(known.get(url, 0) or 0):
                if opened >= MAX_DETAIL:
                    _unread(p, known)  # a later poll reads its comments
                    continue
                opened += 1
                for c in tab.js(IG_COMMENTS) or []:
                    if c["handle"].lower() == handle.lower() and " ".join(c["text"].split()) == " ".join(m["caption"].split()):
                        continue  # the caption, shown like a first comment
                    inbox.append({"id": "ig:" + _id(url, c["handle"], c["text"]), "type": "comment", "post_url": url,
                                  "item_url": c.get("link") or url, "author_handle": c["handle"], "author_name": c["handle"],
                                  "text": c["text"], "created_at": _ts(c.get("at")), "mine": c["handle"].lower() == handle.lower()})
        posts += [{"url": u, "text": "", "created_at": None, "likes": None, "replies": None, "reposts": None, "quotes": None,
                   "views": None, "impressions": None} for u in urls[IG_DETAIL:]]
    finally:
        tab.close()
    return {"inbox": inbox, "posts": posts, "profile": profile}


# ── LinkedIn: posts (OpenCLI, links from the activity page), comments from each post's page, mentions from notifications ──

#: post ids on the activity page, in page order (the posts reader returns new posts without a link)
LI_URNS = r"""[...new Set([...document.querySelectorAll('main a[href]')].map(a => (a.href.match(/urn:li:(?:activity|share|ugcPost):\d+/) || [])[0]).filter(Boolean))]"""
#: each comment's block: the nearest element around its "more options" button that holds its own Reply button
LI_COMMENTS = r"""(() => {
  const out = [];
  for (const b of document.querySelectorAll('[aria-label^="View more options for"][aria-label*="comment"], [aria-label^="Zobrazit další možnosti"][aria-label*="koment"]')) {
    let e = b, i = 0;
    while (e && i < 15 && ![...e.querySelectorAll('button')].some(x => /^(reply|odpovědět)$/i.test((x.innerText || x.getAttribute('aria-label') || '').trim()))) { e = e.parentElement; i++; }
    if (!e) continue;
    out.push({lines: (e.innerText || '').split('\n').map(t => t.trim()).filter(Boolean),
              profile: ((e.querySelector('a[href*="/in/"], a[href*="/company/"]') || {}).href || '').split('?')[0]});
  }
  return out; })()"""
LI_NOTES = r"""[...document.querySelectorAll('main article, main .nt-card, main [data-urn]')].map(c => ({
  text: (c.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 600),
  link: ([...c.querySelectorAll('a[href*="/feed/update/"], a[href*="commentUrn"], a[href*="/posts/"]')].map(a => a.href)[0] || ''),
  who: ((c.querySelector('a[href*="/in/"] span[aria-hidden=true], strong') || {}).innerText || '').trim()
})).filter(n => n.link && /mentioned|zmínil/i.test(n.text)).slice(0, 30)"""
_LI_TIME = re.compile(r"^(\d+\s*(s|m|h|d|w|mo|yr|min|hod|d|t|r|měs)\.?|now|teď)(\s*•.*)?$", re.I)
_LI_STOP = re.compile(r"impressions?|zobrazení|^\d+$|^(like|reply|odpovědět|to se mi líbí|liked by|celebrate)$", re.I)
_LI_MARK = re.compile(r"^(author|autor|•?\s*(you|vy|1st|2nd|3rd\+?|1\.|2\.|3\.))$", re.I)


def li_comment(lines: list) -> tuple:
    """['Name', 'Author', 'headline', '2h', 'the text', '4 impressions', '4'] -> (name, text)."""
    if not lines:
        return "", ""
    name, rest = lines[0], [x for x in lines[1:] if not _LI_MARK.match(x)]
    at = next((i for i, x in enumerate(rest) if _LI_TIME.match(x.replace("(edited)", "").replace("(upraveno)", "").strip())), None)
    if at is None:
        return name, ""
    text = []
    for x in rest[at + 1:]:
        if _LI_STOP.search(x):
            break
        text.append(x)
    return name, "\n".join(text).strip()


def linkedin(acc: dict, known: dict, page: bool = False) -> dict:
    from agent_reach import outbox
    from agent_reach.backends.opencli import opencli_call
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    base = f"https://www.linkedin.com/company/{acc['handle']}/" if page else f"https://www.linkedin.com/in/{acc['handle']}/"
    found = rows(opencli_call("linkedin", ["posts", "--profile-url", base, "--limit", "20"], timeout=240))
    profile = {}
    if not page:
        a = (rows(opencli_call("linkedin", ["profile-analytics", "--profile-url", base], timeout=180)) or [{}])[0]
        profile = {"followers": _num(a.get("followers")), "profile_views": _num(a.get("profile_views")),
                   "impressions": _num(a.get("post_impressions")), "connections": _num(a.get("connections"))}
    tab = outbox.Tab(base + ("posts/" if page else "recent-activity/all/"), "linkedin")
    inbox = []
    try:
        tab.wait("document.querySelector('main')", 30)
        time.sleep(5)
        urns = tab.js(LI_URNS) or []
        posts = []
        for i, p in enumerate(found):
            url = p.get("url") or ""
            if not url and i < len(urns):  # the reader lists the same page in the same order
                url = f"https://www.linkedin.com/feed/update/{urns[i]}/"
            posts.append({"url": url, "text": p.get("body") or p.get("raw_text", ""), "created_at": _ts(p.get("posted_at")),
                          "likes": _num(p.get("reactions")), "replies": _num(p.get("comments")), "reposts": _num(p.get("reposts")),
                          "quotes": None, "views": None, "impressions": _num(p.get("impressions"))})
        for p in _pick(posts, known):
            tab.js(f"location.href = {json.dumps(p['url'])}")
            time.sleep(2)
            tab.wait("document.querySelector('main')", 30)
            time.sleep(5)
            got = tab.js(LI_COMMENTS) or []
            if not got:
                _unread(p, known)
                continue
            for c in got:
                name, text = li_comment(c["lines"])
                if not name:
                    continue
                inbox.append({"id": "li:" + _id(p["url"], name, text), "type": "comment", "post_url": p["url"], "item_url": p["url"],
                              "author_handle": c["profile"].rstrip("/").rsplit("/", 1)[-1], "author_name": name, "text": text,
                              "created_at": None, "mine": c["profile"].rstrip("/") == base.rstrip("/") or name == acc.get("label")})
        tab.js("location.href = " + json.dumps(f"https://www.linkedin.com/company/{acc['handle']}/admin/notifications/all/" if page
                                               else "https://www.linkedin.com/notifications/"))
        time.sleep(2)
        tab.wait("document.querySelector('main')", 30)
        time.sleep(4)
        for n in tab.js(LI_NOTES) or []:  # mentions only: comments come from the posts above
            inbox.append({"id": "li:" + _id(n["link"], n["text"][:120]), "type": "mention", "post_url": "", "item_url": n["link"],
                          "author_handle": "", "author_name": n.get("who", ""), "text": n["text"], "created_at": None, "mine": False})
    finally:
        tab.close()
    return {"inbox": inbox, "posts": posts, "profile": profile}


# ── Facebook: posts and their comments from the profile or Page itself; mentions from notifications ──

FB_FOLLOWERS = r"""(() => { const t = (document.querySelector('[role=main]') || document.body).innerText;
  const m = t.match(/([\d\s.,]+\s*(?:tis\.|K|M)?)\s*(sledující|followers)/i); return m ? m[1] : null; })()"""
#: our posts on the page, found by their comment button ("Okomentovat příspěvek od X, 3 komentáře")
FB_POSTS = r"""[...document.querySelectorAll('[role=main] [aria-label^="Okomentovat příspěvek od"], [role=main] [aria-label^="Leave a comment"], [role=main] [aria-label^="Comment on"]')].map(b => {
  let e = b, i = 0; while (e && i < 25 && !e.querySelector('a[href*="/posts/"], a[href*="story_fbid"], a[href*="permalink"]')) { e = e.parentElement; i++; }
  const link = e ? [...e.querySelectorAll('a[href]')].map(a => a.href.split('?')[0]).find(h => /\/posts\/|story_fbid|permalink/.test(h)) : '';
  const label = b.getAttribute('aria-label') || '';
  return {url: link || '', comments: (label.match(/(\d[\d\s.,]*)\s*(koment|comment)/i) || [])[1] || '0', by: (label.match(/(?:od|by|on)\s+(.+?)(?:,|$)/i) || [])[1] || ''};
}).filter(p => p.url).filter((p, i, a) => a.findIndex(q => q.url === p.url) === i).slice(0, 12)"""
#: comments and replies on the open page: author from the label, the comment's own link (post link + comment_id)
FB_COMMENTS = r"""[...document.querySelectorAll('[role=article][aria-label^="Komentář od"], [role=article][aria-label^="Odpověď od"], [role=article][aria-label^="Comment by"], [role=article][aria-label^="Reply by"]')].map(a => ({
  label: a.getAttribute('aria-label') || '', text: a.innerText || '',
  link: ([...a.querySelectorAll('a[href*="comment_id="]')].map(x => x.href).find(h => /\/posts\/|story_fbid|permalink|\/videos\/|\/photo/.test(h)) || '')
})).slice(0, 60)"""
_FB_WHO = re.compile(r"^(?:Komentář|Odpověď|Comment|Reply)\s+(?:od|by)\s+(.+?)(?:\s+na\s+|\s+to\s+|\s*\()", re.I)
_FB_END = re.compile(r"^(odpovědět|reply|to se mi líbí|like|sdílet|share|upraveno|edited|přeložit|see translation|zobrazit překlad)$", re.I)
_FB_TIME = re.compile(r"^(\d+\s*\S{1,6}|teď|just now|včera|yesterday)$", re.I)


def fb_comment(label: str, text: str, link: str) -> dict:
    """One comment article -> author, text, clean link (post + comment_id) and id."""
    m = _FB_WHO.match(label)
    who = m.group(1).strip() if m else ""
    lines = [x.strip() for x in text.replace(" ", " ").split("\n")]
    lines = [x for x in lines if x and x != "·"]
    if lines and lines[0] == who:
        lines = lines[1:]
    if lines and _FB_TIME.match(lines[0]):
        lines = lines[1:]
    body = []
    for x in lines:
        if _FB_END.match(x):
            break
        body.append(x)
    cid = (re.search(r"[?&]comment_id=(\d+)", link) or [None, None])[1]
    reply = (re.search(r"[?&]reply_comment_id=(\d+)", link) or [None, None])[1]
    post = link.split("?")[0]
    return {"who": who, "text": "\n".join(body).strip(), "post": post,
            "link": f"{post}?comment_id={cid}" + (f"&reply_comment_id={reply}" if reply else "") if cid else post,
            "id": "fb:" + (reply or cid or _id(post, who, " ".join(body)))}


def facebook(acc: dict, known: dict, page: bool = False) -> dict:
    from agent_reach import outbox
    from agent_reach.backends.opencli import opencli_call
    rows = lambda x: x if isinstance(x, list) else (x or {}).get("data", []) if isinstance(x, dict) else []  # noqa: E731
    inbox = []
    for n in rows(opencli_call("facebook", ["notifications", "--limit", "30"], timeout=180)):
        text = str(n.get("text", ""))
        if not re.search(r"zmínil|označil|mentioned|tagged", text.lower()):
            continue  # comments come from our posts below (ours included)
        who = re.split(r"\s(?:vás zmínil|zmínil|vás označil|označil|mentioned|tagged)", text, maxsplit=1)[0]
        inbox.append({"id": "fb:" + str(n.get("notif_id") or _id(n.get("url"), text)), "type": "mention", "post_url": "",
                      "item_url": n.get("url", ""), "author_handle": "", "author_name": who[:80], "text": text, "created_at": None, "mine": False})
    me = acc.get("label", "")
    tab = outbox.Tab(f"https://www.facebook.com/{acc['handle']}" if page else "https://www.facebook.com/me", "facebook")
    profile = {}
    try:
        tab.wait("document.querySelector('[role=main]')", 30)
        time.sleep(4)
        if page:
            profile = {"followers": _num(tab.js(FB_FOLLOWERS))}
        for _ in range(3):  # the timeline loads as it scrolls, like a person looking down their own wall
            tab.js("window.scrollBy(0, 1200)")
            time.sleep(2.5)
        posts = [{"url": p["url"], "text": "", "created_at": None, "likes": None, "replies": _num(p["comments"]), "reposts": None,
                  "quotes": None, "views": None, "impressions": None} for p in (tab.js(FB_POSTS) or [])]
        for p in _pick(posts, known):
            tab.js(f"location.href = {json.dumps(p['url'])}")
            time.sleep(2)
            tab.wait("document.querySelector('[role=main]')", 30)
            time.sleep(4)
            got = tab.js(FB_COMMENTS) or []
            if not got:
                _unread(p, known)
                continue
            for a in got:
                c = fb_comment(a["label"], a["text"], a["link"] or p["url"])
                if not c["who"]:
                    continue
                inbox.append({"id": c["id"], "type": "reply" if a["label"].lower().startswith(("odpověď", "reply")) else "comment",
                              "post_url": p["url"], "item_url": c["link"], "author_handle": "", "author_name": c["who"],
                              "text": c["text"], "created_at": None, "mine": c["who"] == me})
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
