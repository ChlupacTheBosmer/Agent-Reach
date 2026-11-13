"""Fork: posts and messages that go out only after the user edits and approves the exact text.

    agent-reach-outbox new <kind> --target <url> [--expected-name NAME] [--text TEXT | --text-file FILE]
    agent-reach-outbox list                 drafts with status and hash
    agent-reach-outbox show <draft>         kind, target, expected name, full text and its hash
    agent-reach-outbox check <draft>        dry run on the real page: recipient/group verified, nothing published
    agent-reach-outbox send <draft> --sha <hash>
                                            send it. A Claude Code hook asks the user first and shows the exact text;
                                            the hash proves the text sent is the text approved.

Kinds:
    facebook-group-post   target = group URL; expected-name = group name (checked on the page)
    facebook-message      target = profile URL, /user/<id>/ link or messages/t/<id>; expected-name = person's name
    linkedin-message      target = messaging thread URL; expected-name = name in the thread header (OpenCLI safe-send)
    linkedin-connect      target = profile URL; expected-name = profile name; text = note, max 300 characters
    twitter-post          target = your profile URL (x.com/<you>); expected-name = your handle (checked)
    twitter-reply         target = the post URL; expected-name = its author's handle (checked)
    bluesky-post          target = your profile URL (bsky.app/profile/<you>); expected-name = your handle
    bluesky-reply         target = the post URL; expected-name = its author's handle
    bluesky-message       target = the person's profile URL; expected-name = their handle or display name
                          (Bluesky uses the official API and an app password with direct-message access:
                          `agent-reach configure bluesky-handle`, `agent-reach configure bluesky-app-password`)

Drafts live in ~/.agent-reach/outbox/<id>.md (header, a line '---', then the text). The user edits the text there.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path

KINDS = ("facebook-group-post", "facebook-page-post", "facebook-message", "linkedin-message", "linkedin-connect",
         "twitter-post", "twitter-reply", "bluesky-post", "bluesky-reply", "bluesky-message",
         "instagram-post", "instagram-reel", "instagram-story")
HOSTS = {"facebook": "facebook.com", "linkedin": "linkedin.com", "twitter": "x.com", "bluesky": "bsky.app",
         "instagram": "instagram.com"}
FIELDS = ("kind", "target", "expected_name", "status", "created", "result")
#: write budget per site: minimum gap (s), random extra (s), per day; separate from reads
WRITE_LIMITS = {f"{site}:write": (90, 60, 10) for site in ("facebook", "linkedin", "twitter", "bluesky", "instagram")}


def root() -> Path:
    from agent_reach.runtime import state
    return state.root() / "outbox"


# ── draft files ──

def parse(text: str) -> dict:
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise ValueError("draft has no '---' line between the header and the text")
    d = {k: "" for k in FIELDS}
    for line in head.splitlines():
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            if k.strip() in FIELDS:
                d[k.strip()] = v.strip()
    d["text"] = body.strip("\n")
    return d


def render(d: dict) -> str:
    head = "\n".join(f"{k}: {d.get(k, '')}" for k in FIELDS)
    return (f"# Edit the text below the --- line, save, then ask the agent to send it.\n{head}\n---\n"
            f"{d['text'].strip()}\n")


def digest(d: dict) -> str:
    """Hash of everything that decides what goes out where."""
    raw = "\x1f".join([d["kind"], d["target"], d["expected_name"], d["text"].strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def path_of(draft: str) -> Path:
    p = Path(draft)
    if not p.is_absolute():
        p = root() / (draft if draft.endswith(".md") else draft + ".md")
    if not p.is_file():
        raise SystemExit(f"no draft {draft} (see `agent-reach-outbox list`)")
    return p


def load(draft: str) -> tuple[Path, dict]:
    p = path_of(draft)
    return p, parse(p.read_text("utf-8"))


def validate(d: dict) -> None:
    if d["kind"] not in KINDS:
        raise SystemExit(f"unknown kind {d['kind']!r}; one of {', '.join(KINDS)}")
    if not d["text"].strip() and d["kind"] != "instagram-story":  # a story is only the picture or video
        raise SystemExit("the draft has no text")
    if not d["expected_name"]:
        raise SystemExit("expected_name is required: the name the page must show, so nothing goes to the wrong place")
    host = HOSTS[d["kind"].split("-")[0]]
    if host not in d["target"].replace("twitter.com", "x.com"):
        raise SystemExit(f"target must be a {host} URL")
    if d["kind"].startswith(("twitter", "bluesky")) and d["kind"] != "bluesky-message" and len(d["text"]) > (280 if d["kind"].startswith("twitter") else 300):
        raise SystemExit(f"too long for {d['kind'].split('-')[0]} ({len(d['text'])} characters)")
    if d["kind"] == "linkedin-connect" and len(d["text"]) > 300:
        raise SystemExit(f"a LinkedIn connection note is at most 300 characters (this one has {len(d['text'])})")


# ── the browser tab ──

class Tab:
    """One page in the logged-in Agent Reach browser, for the multi-step write flows."""

    def __init__(self, url: str, site: str):
        from agent_reach.runtime import browser, cdp, pacing
        self.cdp = cdp
        pacing.before(site)
        browser.ensure_running()
        self.old, cdp._TIMEOUT = cdp._TIMEOUT, 30
        self.id = (cdp.call("Target.createTarget", {"url": url}) or {}).get("targetId")
        if not self.id:
            raise SystemExit(f"could not open {url} in the Agent Reach browser")

    def js(self, expression: str):
        res = self.cdp.call("Runtime.evaluate", {"expression": f"JSON.stringify(({expression}))", "returnByValue": True,
                                                 "awaitPromise": True}, target_id=self.id) or {}
        raw = (res.get("result") or {}).get("value")
        return json.loads(raw) if raw else None

    def wait(self, expression: str, seconds: float = 25) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.js(f"!!({expression})"):
                return True
            time.sleep(1.5)
        return False

    def type_lines(self, text: str, *, newline_shift: bool) -> None:
        """Insert text into the focused editor; lines are joined with (Shift+)Enter like a person typing."""
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                self.cdp.call("Input.insertText", {"text": line}, target_id=self.id)
            if i < len(lines) - 1:
                self.key("Enter", shift=newline_shift)
            time.sleep(0.3)

    def key(self, name: str, shift: bool = False) -> None:
        mods = 8 if shift else 0
        for kind in ("keyDown", "keyUp"):
            self.cdp.call("Input.dispatchKeyEvent", {"type": kind, "key": name, "code": name, "modifiers": mods,
                                                     "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13},
                          target_id=self.id)

    def upload(self, expression: str, paths: list) -> None:
        """Put local files into the file input that `expression` returns, as a person picking them would."""
        res = self.cdp.call("Runtime.evaluate", {"expression": expression}, target_id=self.id) or {}
        oid = (res.get("result") or {}).get("objectId")
        if not oid:
            raise SystemExit("no file field to attach the media to; nothing was posted")
        self.cdp.call("DOM.setFileInputFiles", {"files": paths, "objectId": oid}, target_id=self.id)

    def close(self) -> None:
        self.cdp.call("Target.closeTarget", {"targetId": self.id})
        self.cdp._TIMEOUT = self.old
        from agent_reach.runtime import browser
        browser.save_session()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ── Facebook ──

COMPOSER_BUTTON = r"""[...document.querySelectorAll('[role=button]')].find(b => /^(napište něco|write something|vytvořte veřejný příspěvek|create a public post)/i.test((b.innerText || '').trim()))"""
COMPOSER_BOX = r"""[...document.querySelectorAll('[role=dialog] [role=textbox][contenteditable=true]')].find(x => /napište|write|something/i.test(x.getAttribute('aria-placeholder') || x.getAttribute('aria-label') || ''))"""
POST_BUTTON = r"""[...document.querySelectorAll('[role=dialog] [role=button][aria-label]')].find(b => /^(zveřejnit|post|publikovat)$/i.test(b.getAttribute('aria-label')))"""


#: Facebook shows this to a person who manages a Page but has not switched into it
SWITCH_PROMPT = r"/Switch now|Switch into|Přepnout teď|Přepnout na|Přepnout do/i.test((document.querySelector('[role=main]')||document.body).innerText)"
PHOTO_BUTTON = r"""[...document.querySelectorAll('[role=dialog] [role=button][aria-label]')].find(b => /photo|fotk/i.test(b.getAttribute('aria-label')))"""
DIALOG_FILE = "document.querySelector('[role=dialog] input[type=file]')"


def facebook_group_post(d: dict, send: bool) -> str:
    """A group post, or (facebook-page-post) a post on the Page this browser is switched into."""
    page = d["kind"] == "facebook-page-post"
    tab = Tab(d["target"], "facebook")
    try:
        if page and tab.wait(SWITCH_PROMPT, 8):
            raise SystemExit("this browser is logged in as a person, not as the Page: switch into the Page once in the "
                             "login view (profile menu > the Page), then try again; nothing was posted")
        if not tab.wait(COMPOSER_BUTTON, 30):
            raise SystemExit("no 'Write something' box on the page" if page else
                             "no 'Write something' box on the group page (not a member, or posting is closed)")
        if _norm(d["expected_name"]).lower() not in _norm(tab.js("document.title + ' ' + (document.querySelector('[role=main]')||document.body).innerText.slice(0, 3000)") or "").lower():
            raise SystemExit(f"the page does not show the group name {d['expected_name']!r}; refusing to post")
        tab.js(f"(() => {{ const b = {COMPOSER_BUTTON}; b.click(); return true; }})()")
        if not tab.wait(COMPOSER_BOX, 30):
            raise SystemExit("the post editor did not open")
        tab.js(f"(() => {{ const x = {COMPOSER_BOX}; x.focus(); x.click(); return true; }})()")
        tab.type_lines(d["text"], newline_shift=False)
        time.sleep(1.5)
        typed = tab.js(f"(() => {{ const x = {COMPOSER_BOX}; return x ? x.innerText : ''; }})()") or ""
        if _norm(d["text"])[:200] not in _norm(typed):
            raise SystemExit("the editor does not contain the approved text; nothing was posted")
        images = [m["path"] for m in d.get("media") or []]
        if images:
            if not tab.js(f"!!({DIALOG_FILE})"):
                tab.js(f"(() => {{ const b = {PHOTO_BUTTON}; b && b.click(); return !!b; }})()")
            if not tab.wait(DIALOG_FILE, 15):
                raise SystemExit("the editor has no photo field; nothing was posted")
            before = tab.js("document.querySelectorAll('[role=dialog] img').length") or 0
            tab.upload(DIALOG_FILE, images)
            if not tab.wait(f"document.querySelectorAll('[role=dialog] img').length >= {before + len(images)}", 60):
                raise SystemExit("the photos did not appear in the editor; nothing was posted")
            time.sleep(2)
        if not tab.js(f"!!({POST_BUTTON})"):
            raise SystemExit("no Post button in the editor")
        if not send:
            _discard(tab)
            return (f"check ok: {'Page' if page else 'group'} found, editor opened, text"
                    f"{' and photos' if images else ''} in, Post button present; draft discarded, nothing posted")
        tab.js(f"(() => {{ {POST_BUTTON}.click(); return true; }})()")
        if not tab.wait(f"!({COMPOSER_BOX})", 40):
            raise SystemExit("the editor did not close after Post; check the group in `agent-reach login facebook`")
        time.sleep(3)
        pending = tab.js(r"/čeká na schválení|pending|awaiting approval|schválit/i.test(document.body.innerText)")
        return "posted, waiting for admin approval" if pending else "posted"
    finally:
        tab.close()


def _discard(tab: Tab) -> None:
    tab.js(r"""(() => { const c = [...document.querySelectorAll('[role=dialog] [role=button][aria-label]')].find(b => /zavřít|close/i.test(b.getAttribute('aria-label'))); c && c.click(); return !!c; })()""")
    time.sleep(2)
    tab.js(r"""(() => { const b = [...document.querySelectorAll('[role=dialog] [role=button]')].find(b => /^(zahodit|discard|leave|odejít)/i.test((b.getAttribute('aria-label') || b.innerText || '').trim())); b && b.click(); return !!b; })()""")
    time.sleep(1.5)


MESSAGE_BOX = r"""[...document.querySelectorAll('[role=main] [role=textbox][contenteditable=true]')].find(x => /zpráv|message|aa/i.test(x.getAttribute('aria-label') || x.getAttribute('aria-placeholder') || '')) || document.querySelector('[role=main] [role=textbox][contenteditable=true]')"""


def _messenger_url(target: str) -> str:
    m = re.search(r"/user/(\d+)", target) or re.search(r"/messages/t/([\w.]+)", target) \
        or re.search(r"[?&]id=(\d+)", target) or re.search(r"facebook\.com/([A-Za-z0-9.]{3,})/?$", target.split("?")[0])
    if not m:
        raise SystemExit("facebook-message target must be a profile URL, a /user/<id>/ link or a messages/t/<id> URL")
    return f"https://www.facebook.com/messages/t/{m.group(1)}/"


def facebook_message(d: dict, send: bool) -> str:
    tab = Tab(_messenger_url(d["target"]), "facebook")
    try:
        if not tab.wait(f"({MESSAGE_BOX}) || /PIN/.test([...document.querySelectorAll('[role=dialog]')].map(d => d.innerText).join(' '))", 35) \
                or tab.js(r"/PIN/.test([...document.querySelectorAll('[role=dialog]')].map(d => d.innerText).join(' '))"):
            if tab.js(r"/PIN/.test([...document.querySelectorAll('[role=dialog]')].map(d => d.innerText).join(' '))"):
                raise SystemExit("Messenger asks for the user's chat PIN in this browser. The user (never the agent) runs "
                                 "`agent-reach login facebook`, opens facebook.com/messages in that window, enters the PIN, "
                                 "then `agent-reach browser stop`.")
            raise SystemExit("no message box in the conversation (the person may not accept messages)")
        header = _norm(tab.js("(document.querySelector('[role=main]')||document.body).innerText.slice(0, 1500)") or "")
        if _norm(d["expected_name"]).lower() not in header.lower():
            raise SystemExit(f"the conversation does not show {d['expected_name']!r}; refusing to send")
        if not send:  # no typing in a dry run: the other person would see a typing indicator
            return "check ok: conversation with the expected person, message box present; nothing typed or sent"
        tab.js(f"(() => {{ const x = {MESSAGE_BOX}; x.focus(); x.click(); return true; }})()")
        tab.type_lines(d["text"], newline_shift=True)
        time.sleep(1)
        typed = tab.js(f"(() => {{ const x = {MESSAGE_BOX}; return x ? x.innerText : ''; }})()") or ""
        if _norm(d["text"])[:200] not in _norm(typed):
            tab.js(f"(() => {{ const x = {MESSAGE_BOX}; x.focus(); document.execCommand('selectAll'); document.execCommand('delete'); return true; }})()")
            raise SystemExit("the message box does not contain the approved text; cleared it, nothing was sent")
        tab.key("Enter")
        time.sleep(4)
        shown = _norm(tab.js("(document.querySelector('[role=main]')||document.body).innerText.slice(-3000)") or "")
        return "sent" if _norm(d["text"])[-60:] in shown else "sent (could not confirm it in the conversation view)"
    finally:
        tab.close()


# ── LinkedIn (OpenCLI's fail-closed senders) ──

def linkedin(d: dict, send: bool) -> str:
    from agent_reach.backends.opencli import opencli_call
    if d["kind"] == "linkedin-message":
        args = ["safe-send", "--thread-url", d["target"], "--expected-name", d["expected_name"], "--message", d["text"]]
    else:
        args = ["connect", d["target"], "--expected-name", d["expected_name"], "--note", d["text"]]
    out = opencli_call("linkedin", args + (["--send"] if send else []), timeout=180)
    return ("sent: " if send else "check ok (OpenCLI dry run): ") + json.dumps(out, ensure_ascii=False)[:300]


# ── X / Twitter (OpenCLI's post and reply; never its batch `reply-dm`) ──

def _handle(s: str) -> str:
    return s.strip().lstrip("@").lower()


def twitter(d: dict, send: bool) -> str:
    from agent_reach.backends.opencli import opencli_call
    want = _handle(d["expected_name"])
    if d["kind"] == "twitter-reply":
        m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)", d["target"])
        if not m or m.group(1).lower() != want:
            raise SystemExit(f"the reply target is not a post by @{want}; refusing")
    else:
        tab = Tab("https://x.com/home", "twitter")
        try:
            tab.wait("document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]')", 30)
            me = tab.js("(document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]') || {}).innerText || ''") or ""
        finally:
            tab.close()
        if f"@{want}" not in me.lower():
            raise SystemExit(f"the browser is not logged in to X as @{want} (it shows {_norm(me)[:60]!r}); refusing")
    if not send:
        return f"check ok: X account / target verified for @{want}; nothing posted"
    args = ["reply", d["target"], d["text"]] if d["kind"] == "twitter-reply" else ["post", d["text"]]
    images = [m["path"] for m in d.get("media") or []]
    if len(images) > 4:
        raise SystemExit("X takes at most 4 images per post")
    if images and d["kind"] == "twitter-post":
        args += ["--images", ",".join(images)]
    elif images:
        args += ["--image", images[0]]
    out = opencli_call("twitter", args, timeout=180)
    return "posted: " + json.dumps(out, ensure_ascii=False)[:300]


# ── Bluesky (official AT Protocol API with an app password) ──

def _bsky(method: str, path: str, body=None, token: str = "", proxy: str = "", base: str = "https://bsky.social",
          raw: bytes = None, mime: str = "application/json"):
    import urllib.error
    import urllib.request
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"{base}/xrpc/{path}", method=method, data=data,
                                 headers={"Content-Type": mime, "User-Agent": "agent-reach-outbox",
                                          **({"Authorization": f"Bearer {token}"} if token else {}),
                                          **({"atproto-proxy": proxy} if proxy else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Bluesky {path}: HTTP {exc.code} {exc.read()[:200]!r}") from None


def _bsky_session() -> dict:
    from agent_reach.config import Config
    cfg = Config(read_only=True)
    handle, password = cfg.get("bluesky_handle"), cfg.get("bluesky_app_password")
    if not handle or not password:
        raise SystemExit("Bluesky needs an app password: the user runs `agent-reach configure bluesky-handle` and "
                         "`agent-reach configure bluesky-app-password` (Settings > Privacy and security > App passwords; "
                         "tick direct messages for bluesky-message)")
    return _bsky("POST", "com.atproto.server.createSession", {"identifier": handle, "password": password})


def _resolve_handle(handle: str):
    """A Bluesky handle -> its DID through the public API, or None when it does not exist."""
    import urllib.error
    import urllib.request
    try:
        url = f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "agent-reach-outbox"}), timeout=20) as r:
            return json.loads(r.read()).get("did")
    except (urllib.error.URLError, ValueError, OSError):
        return None


def _facets(text: str, resolve=None) -> list:
    """Clickable links and real @mentions: Bluesky needs byte offsets (and, for mentions, the account's DID)."""
    resolve = resolve or _resolve_handle

    def span(start: int, end: int) -> dict:
        return {"byteStart": len(text[:start].encode("utf-8")), "byteEnd": len(text[:end].encode("utf-8"))}
    out = []
    for m in re.finditer(r"https?://[^\s)\]>\"']+", text):
        out.append({"index": span(m.start(), m.end()), "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}]})
    for m in re.finditer(r"(?<![\w@/.])@([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)", text):
        did = resolve(m.group(1).rstrip(".").lower())
        if did:  # an unknown handle stays plain text
            out.append({"index": span(m.start(), m.start() + 1 + len(m.group(1).rstrip("."))),
                        "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}]})
    return out


def bluesky(d: dict, send: bool) -> str:
    from agent_reach.channels.bluesky import bsky_uri
    from agent_reach.runtime import pacing
    pacing.before("bluesky")
    sess = _bsky_session()
    token, me = sess["accessJwt"], sess["handle"].lower()
    want = _handle(d["expected_name"])
    record = {"$type": "app.bsky.feed.post", "text": d["text"], "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
              "facets": _facets(d["text"])}
    if d["kind"] == "bluesky-post":
        if me != want:
            raise SystemExit(f"the app password belongs to @{me}, not @{want}; refusing")
    elif d["kind"] == "bluesky-reply":
        uri = bsky_uri(d["target"])
        posts = _bsky("GET", f"app.bsky.feed.getPosts?uris={uri}", token=token).get("posts") or []
        if not posts:
            raise SystemExit("the post to reply to was not found")
        parent = posts[0]
        if parent["author"]["handle"].lower() != want:
            raise SystemExit(f"the post is by @{parent['author']['handle']}, not @{want}; refusing")
        ref = {"uri": parent["uri"], "cid": parent["cid"]}
        record["reply"] = {"root": (parent.get("record") or {}).get("reply", {}).get("root", ref), "parent": ref}
    else:  # bluesky-message
        actor = re.search(r"bsky\.app/profile/([^/?#]+)", d["target"])
        if not actor:
            raise SystemExit("bluesky-message target must be a bsky.app/profile/<handle> URL")
        prof = _bsky("GET", f"app.bsky.actor.getProfile?actor={actor.group(1)}", token=token)
        names = {prof.get("handle", "").lower(), _norm(prof.get("displayName", "")).lower()}
        if want not in names and _norm(d["expected_name"]).lower() not in names:
            raise SystemExit(f"the profile is @{prof.get('handle')} ({prof.get('displayName')}), not {d['expected_name']!r}")
        chat = "did:web:api.bsky.chat#bsky_chat"
        convo = _bsky("GET", f"chat.bsky.convo.getConvoForMembers?members={prof['did']}", token=token, proxy=chat)["convo"]
        if not send:
            return f"check ok: logged in as @{me}; conversation with @{prof['handle']} available; nothing sent"
        _bsky("POST", "chat.bsky.convo.sendMessage", {"convoId": convo["id"], "message": {"text": d["text"],
              "facets": _facets(d["text"])}}, token=token, proxy=chat)
        return f"sent to @{prof['handle']}"
    media = d.get("media") or []
    if len(media) > 4:
        raise SystemExit("Bluesky takes at most 4 images per post")
    for m in media:
        if Path(m["path"]).stat().st_size > 1_000_000:
            raise SystemExit(f"{Path(m['path']).name} is over Bluesky's 1 MB image limit; resize it first")
    if not send:
        return f"check ok: logged in as @{me}; target verified; nothing posted"
    if media:
        images = []
        for m in media:
            blob = _bsky("POST", "com.atproto.repo.uploadBlob", token=token, raw=Path(m["path"]).read_bytes(),
                         mime=m.get("mime") or "image/jpeg")["blob"]
            images.append({"alt": m.get("alt") or "", "image": blob})
        record["embed"] = {"$type": "app.bsky.embed.images", "images": images}
    out = _bsky("POST", "com.atproto.repo.createRecord", {"repo": sess["did"], "collection": "app.bsky.feed.post",
                                                          "record": record}, token=token)
    rkey = out["uri"].rsplit("/", 1)[-1]
    return f"posted: https://bsky.app/profile/{me}/post/{rkey}"


# ── Instagram (OpenCLI's post, reel and story in the logged-in browser) ──

#: the sidebar's Profile link points to the logged-in account
IG_ME = r"""[...document.querySelectorAll('a[href]')].filter(a => /^(profile|profil)$/i.test((a.textContent || '').trim())).map(a => a.getAttribute('href')).join(' ')"""


def instagram(d: dict, send: bool) -> str:
    from agent_reach.backends.opencli import opencli_call
    want = _handle(d["expected_name"])
    tab = Tab("https://www.instagram.com/", "instagram")
    try:
        tab.wait(IG_ME, 30)
        me = (tab.js(IG_ME) or "").lower()
    finally:
        tab.close()
    if f"/{want}/" not in me:
        raise SystemExit(f"the browser is not logged in to Instagram as @{want} (it shows {me[:60]!r}); refusing")
    media = [m["path"] for m in d.get("media") or []]
    if not media:
        raise SystemExit("an Instagram post needs a photo or a video")
    if not send:
        return f"check ok: logged in as @{want}, {len(media)} media file(s) ready; nothing posted"
    if d["kind"] == "instagram-reel":
        args = ["reel", "--video", media[0]] + ([d["text"]] if d["text"] else [])
    elif d["kind"] == "instagram-story":
        args = ["story", "--media", media[0]]
    else:
        args = ["post", "--media", ",".join(media)] + ([d["text"]] if d["text"] else [])
    out = opencli_call("instagram", args, timeout=900)
    return "posted: " + json.dumps(out, ensure_ascii=False)[:300]


SENDERS = {"facebook-group-post": facebook_group_post, "facebook-page-post": facebook_group_post,
           "instagram-post": instagram, "instagram-reel": instagram, "instagram-story": instagram,
           "facebook-message": facebook_message,
           "linkedin-message": linkedin, "linkedin-connect": linkedin,
           "twitter-post": twitter, "twitter-reply": twitter,
           "bluesky-post": bluesky, "bluesky-reply": bluesky, "bluesky-message": bluesky}


# ── commands ──

def cmd_new(argv: list) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="agent-reach-outbox new")
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("--target", required=True)
    ap.add_argument("--expected-name", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file")
    a = ap.parse_args(argv)
    text = Path(a.text_file).read_text("utf-8") if a.text_file else a.text
    d = {"kind": a.kind, "target": a.target.strip(), "expected_name": a.expected_name.strip(), "status": "draft",
         "created": dt.datetime.now().isoformat(timespec="seconds"), "result": "", "text": text}
    root().mkdir(parents=True, exist_ok=True)
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{a.kind}"
    p = root() / f"{stamp}.md"
    n = 2
    while p.exists():  # never overwrite another draft
        p = root() / f"{stamp}-{n}.md"
        n += 1
    p.write_text(render(d), "utf-8")
    print(f"draft: {p}\nOpen it, edit the text below the --- line, save. Then: agent-reach-outbox show {p.stem}")
    return 0


def cmd_show(draft: str) -> int:
    p, d = load(draft)
    print(f"draft:    {p}\nkind:     {d['kind']}\ntarget:   {d['target']}\nexpected: {d['expected_name']}\n"
          f"status:   {d['status']}\nsha:      {digest(d)}\n--- text ---\n{d['text']}\n--- end ---")
    return 0


def cmd_list() -> int:
    for p in sorted(root().glob("*.md")) if root().exists() else []:
        d = parse(p.read_text("utf-8"))
        print(f"{p.stem:42} {d['status']:8} {digest(d)}  {d['kind']:20} {d['expected_name'][:30]}")
    return 0


def _run(draft: str, send: bool, sha: str = "") -> int:
    from agent_reach.runtime import pacing
    p, d = load(draft)
    validate(d)
    if send:
        if d["status"] == "sent":
            raise SystemExit("this draft was already sent; make a new draft")
        if sha != digest(d):
            raise SystemExit(f"--sha {sha!r} does not match the draft ({digest(d)}): it changed since it was shown; "
                             "show it again and get a new approval")
        site = d["kind"].split("-")[0] + ":write"
        gap, extra, cap = WRITE_LIMITS[site]
        pacing.LIMITS.setdefault(site, (gap, extra, cap))
        pacing.before(site)  # at most a few posts/messages a day, never in a burst
    try:
        result = SENDERS[d["kind"]](d, send)
    except SystemExit as exc:
        if send:
            d.update(status="failed", result=f"{dt.datetime.now():%Y-%m-%d %H:%M} {exc}")
            p.write_text(render(d), "utf-8")
        raise
    if send:
        d.update(status="sent", result=f"{dt.datetime.now():%Y-%m-%d %H:%M} {result}")
        p.write_text(render(d), "utf-8")
    print(result)
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "new":
        return cmd_new(rest)
    if cmd == "list":
        return cmd_list()
    if cmd == "show" and len(rest) == 1:
        return cmd_show(rest[0])
    if cmd == "check" and len(rest) == 1:
        return _run(rest[0], send=False)
    if cmd == "send" and len(rest) == 3 and rest[1] == "--sha":
        return _run(rest[0], send=True, sha=rest[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
