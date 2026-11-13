"""Meta Graph API driver: V4AIR's Facebook Page and its linked Instagram professional account.

Runs inside the account's home (job.py). The app's Meta developer app works in development mode for the team's own
Page and Instagram account (members with a role on the app). Tokens: the OAuth code is exchanged for a long-lived
user token, then the Page token (which does not expire while the user stays an admin) is stored in this home.
Publisher env: META_APP_ID, META_APP_SECRET; META_GRAPH_VERSION (default v25.0).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com"
SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish,business_management"


def version() -> str:
    return os.environ.get("META_GRAPH_VERSION", "v25.0")


def graph(method: str, path: str, params: dict | None = None):
    params = {k: v for k, v in (params or {}).items() if v is not None}
    url = f"{GRAPH}/{version()}/{path.lstrip('/')}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "v4air-publisher"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            err = json.loads(exc.read()).get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or str(exc)
        except ValueError:
            msg = str(exc)
        raise SystemExit(f"Meta {path.split('?')[0]}: {msg}") from None


def _cfg():
    from agent_reach.config import Config
    return Config()


# ── connecting (OAuth) ──

def connect(network: str, wanted: str, code: str, redirect_uri: str) -> dict:
    """Exchange the OAuth code, pick the Page (and its Instagram account) named `wanted`, store the tokens."""
    app_id, secret = os.environ.get("META_APP_ID"), os.environ.get("META_APP_SECRET")
    if not app_id or not secret:
        raise SystemExit("the publisher has no META_APP_ID / META_APP_SECRET")
    short = graph("GET", "oauth/access_token", {"client_id": app_id, "client_secret": secret,
                                                "redirect_uri": redirect_uri, "code": code})["access_token"]
    user = graph("GET", "oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                               "client_secret": secret, "fb_exchange_token": short})["access_token"]
    pages = graph("GET", "me/accounts", {"access_token": user, "limit": 100,
                                        "fields": "id,name,access_token,instagram_business_account{id,username}"}).get("data", [])
    want = (wanted or "").strip().lstrip("@").lower()
    if network == "instagram":
        hits = [p for p in pages if p.get("instagram_business_account") and
                (not want or p["instagram_business_account"].get("username", "").lower() == want)]
    else:
        hits = [p for p in pages if not want or p["name"].lower() == want]
    if len(hits) != 1:
        names = [f"{p['name']} (@{p['instagram_business_account']['username']})" if p.get("instagram_business_account")
                 else p["name"] for p in pages]
        raise SystemExit(f"could not pick one {'Instagram account' if network == 'instagram' else 'Page'} named "
                         f"{wanted!r}; you manage: {', '.join(names) or 'nothing'}")
    page = hits[0]
    cfg = _cfg()
    cfg.set("meta_page_id", page["id"])
    cfg.set("meta_page_token", page["access_token"])
    if page.get("instagram_business_account"):
        cfg.set("meta_ig_id", page["instagram_business_account"]["id"])
        cfg.set("meta_ig_username", page["instagram_business_account"].get("username", ""))
    handle = page["instagram_business_account"]["username"] if network == "instagram" else page["name"]
    return {"ok": True, "result": f"connected {handle}", "handle": handle}


def check(network: str) -> dict:
    cfg = _cfg()
    token = cfg.get("meta_page_token")
    if not token:
        return {"ok": False, "error": "not connected: press Connect with Meta on the accounts page"}
    if network == "instagram":
        ig = graph("GET", cfg.get("meta_ig_id"), {"fields": "username", "access_token": token})
        return {"ok": True, "result": f"Instagram @{ig.get('username')}"}
    page = graph("GET", cfg.get("meta_page_id"), {"fields": "name", "access_token": token})
    return {"ok": True, "result": f"Facebook Page {page.get('name')}"}


# ── publishing ──

def _ctx(network: str):
    cfg = _cfg()
    token = cfg.get("meta_page_token")
    if not token:
        raise SystemExit("not connected to Meta: press Connect with Meta on the accounts page")
    return cfg, token


def _verify(expected: str, actual: str, what: str) -> None:
    if expected.strip().lstrip("@").lower() != (actual or "").lower():
        raise SystemExit(f"the token belongs to {what} {actual!r}, not {expected!r}; refusing")


def page_post(d: dict) -> str:
    cfg, token = _ctx("facebookpage")
    page_id = cfg.get("meta_page_id")
    _verify(d["expected_name"], graph("GET", page_id, {"fields": "name", "access_token": token}).get("name"), "the Page")
    opts = d.get("options") or {}
    photos = [graph("POST", f"{page_id}/photos", {"url": m["url"], "published": "false", "access_token": token})["id"]
              for m in d.get("media") or []]
    params = {"message": d["text"], "access_token": token, "link": opts.get("link") or None}
    for i, pid in enumerate(photos):
        params[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
    post_id = graph("POST", f"{page_id}/feed", params)["id"]
    if opts.get("first_comment"):
        graph("POST", f"{post_id}/comments", {"message": opts["first_comment"], "access_token": token})
    link = graph("GET", post_id, {"fields": "permalink_url", "access_token": token}).get("permalink_url", "")
    return f"posted: {link or post_id}"


def _tags(usernames: list) -> str | None:
    """Instagram wants a position per tagged person; spread them along the lower part of the image."""
    names = [u.strip().lstrip("@") for u in usernames if u.strip()]
    if not names:
        return None
    return json.dumps([{"username": n, "x": round((i + 1) / (len(names) + 1), 2), "y": 0.85} for i, n in enumerate(names)])


def _wait(container: str, token: str, minutes: float = 10) -> None:
    """Video containers are processed by Instagram before they can be published."""
    end = time.monotonic() + minutes * 60
    while time.monotonic() < end:
        st = graph("GET", container, {"fields": "status_code,status", "access_token": token})
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise SystemExit(f"Instagram could not process the media: {st.get('status')}")
        time.sleep(8)
    raise SystemExit("Instagram did not finish processing the media in 10 minutes")


def instagram(d: dict) -> str:
    cfg, token = _ctx("instagram")
    ig = cfg.get("meta_ig_id")
    _verify(d["expected_name"], graph("GET", ig, {"fields": "username", "access_token": token}).get("username"), "Instagram account")
    opts, media, kind = d.get("options") or {}, d.get("media") or [], d["kind"]
    collaborators = json.dumps([c.strip().lstrip("@") for c in opts.get("collaborators", []) if c.strip()][:3]) \
        if opts.get("collaborators") and kind != "instagram-story" else None
    tags = _tags(opts.get("tag_people") or [])
    is_video = lambda m: (m.get("mime") or "").startswith("video/") or m["url"].lower().endswith((".mp4", ".mov"))  # noqa: E731

    def item(m, extra):
        if is_video(m):  # carousel children are VIDEO, a standalone video is a reel, a story is STORIES
            vtype = "VIDEO" if extra.get("is_carousel_item") else "STORIES" if kind == "instagram-story" else "REELS"
            return {"media_type": vtype, "video_url": m["url"], **extra}
        return {"image_url": m["url"], "alt_text": m.get("alt") or None, **extra}

    if kind == "instagram-carousel":
        children = []
        for m in media:
            c = graph("POST", f"{ig}/media", {**item(m, {"is_carousel_item": "true"}), "user_tags": None if is_video(m) else tags,
                                              "access_token": token})["id"]
            if is_video(m):
                _wait(c, token)
            children.append(c)
        container = graph("POST", f"{ig}/media", {"media_type": "CAROUSEL", "children": ",".join(children),
                                                  "caption": d["text"], "collaborators": collaborators, "access_token": token})["id"]
    else:
        m = media[0]
        extra = {"caption": d["text"] if kind != "instagram-story" else None, "collaborators": collaborators,
                 "user_tags": tags if not is_video(m) or kind == "instagram-story" else None}
        if kind == "instagram-story":
            extra["media_type"] = "STORIES"
        if kind == "instagram-reel":
            extra["share_to_feed"] = "true"
        container = graph("POST", f"{ig}/media", {**item(m, extra), "access_token": token})["id"]
        if is_video(m):
            _wait(container, token)
    media_id = graph("POST", f"{ig}/media_publish", {"creation_id": container, "access_token": token})["id"]
    if opts.get("first_comment") and kind != "instagram-story":
        graph("POST", f"{media_id}/comments", {"message": opts["first_comment"], "access_token": token})
    link = graph("GET", media_id, {"fields": "permalink", "access_token": token}).get("permalink", "")
    return f"posted: {link or media_id}"
