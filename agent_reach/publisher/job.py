"""One publisher step, run as its own process with HOME = the account's home (see core.in_home).

    python -m agent_reach.publisher.job configure|check|publish   (JSON on stdin, one JSON line on stdout)
"""
from __future__ import annotations

import contextlib
import json
import re
import time
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

CONFIG_KEYS = ("proxy", "browser_locale", "browser_timezone", "browser_platform", "bluesky_handle", "bluesky_app_password")
MAX_MEDIA = 25 * 1024 * 1024  # the app's media store keeps at most 25 MB per file


def configure(payload: dict) -> dict:
    from agent_reach.config import Config
    cfg = Config()
    for key, value in payload.items():
        if key not in CONFIG_KEYS:
            raise SystemExit(f"not a setting: {key}")
        cfg.set(key, value)
    return {"ok": True, "result": f"saved {', '.join(sorted(payload))}"}


LOGIN_URL = {"twitter": "https://x.com/login", "facebook": "https://www.facebook.com/",
             "facebookpage": "https://www.facebook.com/", "instagram": "https://www.instagram.com/accounts/login/",
             "linkedin": "https://www.linkedin.com/login", "linkedinpage": "https://www.linkedin.com/login"}
PASSWORD_FIELD = "!!document.querySelector('input[type=password]')"


def network() -> str:
    return os.environ.get("PUBLISHER_ACCOUNT", "").split("-")[0]


def setup_home() -> None:
    """Point this account home at the one shared copy of the tools (PUBLISHER_TOOLS): OpenCLI, the CloakBrowser
    launcher and binary, the OpenCLI extension. The profile, cookies, proxy and pacing stay this account's own."""
    shared = os.environ.get("PUBLISHER_TOOLS")
    if not shared:
        return
    shared, ar = Path(shared), Path.home() / ".agent-reach"
    (ar / "browser").mkdir(parents=True, exist_ok=True)
    for link, target in ((ar / "tools", shared / "tools"), (ar / "cloak-binaries", shared / "cloak-binaries"),
                         (ar / "browser" / "extension", shared / "extension")):
        if not link.exists() and target.exists():
            link.symlink_to(target)
    _drop_stale_profile_lock(ar / "browser" / "profile")
    exe = next(iter(sorted((shared / "cloak-binaries").glob("chromium-*/chrome"))
                    + sorted((shared / "cloak-binaries").glob("chromium-*/Chromium.app/Contents/MacOS/Chromium"))), None)
    from agent_reach.runtime import browser
    if exe and browser._load_state().get("executable") != str(exe):
        browser.update_state(backend="cloakbrowser", profile_backend="cloakbrowser", executable=str(exe),
                             launcher_python=str(shared / "tools" / "cloakbrowser" / "venv" / "bin" / "python"))


def _drop_stale_profile_lock(profile: Path) -> None:
    """Chromium's SingletonLock names "<host>-<pid>". A pod restart leaves one naming the old pod, and Chromium then
    refuses the profile ("in use on another computer"). Only one browser runs per pod, so a lock from another host is stale."""
    import socket
    lock = profile / "SingletonLock"
    try:
        owner = os.readlink(lock)
    except OSError:
        return
    if owner.rsplit("-", 1)[0] != socket.gethostname():
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            with contextlib.suppress(OSError):
                (profile / name).unlink()


VIEW_SIZE = (1280, 880)


def open_view(payload: dict) -> dict:
    """Open the account's browser (headless, through its proxy) on the login page; the server streams it."""
    from agent_reach.runtime import browser, cdp
    browser.ensure_running()
    url = payload.get("url") or LOGIN_URL[network()]
    target = (cdp.call("Target.createTarget", {"url": "about:blank"}) or {}).get("targetId")
    if not target:
        raise SystemExit("could not open a page in the account's browser")
    import time
    time.sleep(1.0)  # let the launcher attach (it answers the proxy's login), then load the page
    # a laptop-sized window: the picture in the app is then about life size (1920 px made it tiny, popups made it huge)
    win = (cdp.call("Browser.getWindowForTarget", {"targetId": target}) or {}).get("windowId")
    if win:
        cdp.call("Browser.setWindowBounds", {"windowId": win, "bounds": {"width": VIEW_SIZE[0], "height": VIEW_SIZE[1], "windowState": "normal"}})
    cdp.call("Page.navigate", {"url": url}, target_id=target)
    browser.touch()
    return {"ok": True, "result": target}


def close_browser(_payload: dict | None = None) -> dict:
    from agent_reach.runtime import browser
    if browser.is_running():
        browser.save_session()
        browser.stop()
    return {"ok": True, "result": "closed"}


def _browser_check() -> dict:
    from agent_reach import outbox
    net = network()
    acc = json.loads(os.environ.get("PUBLISHER_ACCOUNT_JSON") or "{}")
    url = {"twitter": "https://x.com/home", "facebook": "https://www.facebook.com/", "instagram": "https://www.instagram.com/",
           "linkedin": "https://www.linkedin.com/feed/", "linkedinpage": "https://www.linkedin.com/feed/",
           "facebookpage": f"https://www.facebook.com/{acc.get('handle', '')}"}[net]
    tab = outbox.Tab(url, net.replace("page", ""))
    try:
        tab.wait("document.readyState === 'complete'", 30)
        time.sleep(4)
        if tab.js(PASSWORD_FIELD) or tab.js(r"/\/(login|authwall|checkpoint|accounts\/login)/.test(location.pathname)"):
            return {"ok": False, "error": "not logged in: open the login view and log in"}
        if net == "facebookpage":
            if tab.js(outbox.SWITCH_PROMPT):
                return {"ok": False, "error": "logged in as a person: in the login view open the Page and press Switch"}
            if tab.js(outbox.BLOCKING_DIALOG):
                return {"ok": False, "error": "Facebook shows a welcome dialog: answer it in the login view"}
            return ({"ok": True, "result": "acting as the Page"} if tab.js(f"!!({outbox.PAGE_PROMPT})")
                    else {"ok": False, "error": "the Page's own composer is missing: is this login a Page manager?"})
        if net == "instagram":
            me = (tab.js(outbox.IG_ME) or "").lower()
            want = (acc.get("handle") or "").lower()
            if not me:
                return {"ok": False, "error": "not logged in (or Instagram asks about cookies): open the login view"}
            found = re.search(r"/([\w.]+)/", me)
            if not want and found:  # no handle given: the logged-in one becomes the account's handle
                return {"ok": True, "result": f"logged in as @{found.group(1)}", "handle": found.group(1)}
            return {"ok": True, "result": f"logged in as @{want}"} if f"/{want}/" in me else \
                {"ok": False, "error": f"logged in, but not as @{want} ({me[:40]!r})"}
        if net == "twitter":
            me = (tab.js("(document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]') || {}).innerText || ''") or "").lower()
            want = (acc.get("handle") or "").lower()
            if not me:
                return {"ok": False, "error": "not logged in: open the login view and log in"}
            found = re.search(r"@(\w+)", me)
            if not want and found:
                return {"ok": True, "result": f"logged in as @{found.group(1)}", "handle": found.group(1)}
            return {"ok": True, "result": f"logged in as @{want}"} if f"@{want}" in me else \
                {"ok": False, "error": f"logged in, but not as @{want}"}
        if net == "linkedin" and not acc.get("handle"):  # /in/me/ redirects to the member's own profile address
            tab.js("location.href = 'https://www.linkedin.com/in/me/'")
            for _ in range(15):
                time.sleep(1.5)
                found = re.search(r"linkedin\.com/in/([^/?#]+)", tab.js("location.href") or "")
                if found and found.group(1) != "me":
                    return {"ok": True, "result": f"logged in as {found.group(1)}", "handle": found.group(1)}
        return {"ok": True, "result": "logged in"}
    finally:
        tab.close()


def check(_payload: dict) -> dict:
    net = network()
    if net != "bluesky":
        return _browser_check()
    if net == "bluesky":
        from agent_reach import outbox
        from agent_reach.config import Config
        cfg = Config(read_only=True)
        if not cfg.get("bluesky_handle") or not cfg.get("bluesky_app_password"):
            return {"ok": False, "error": "no app password yet: create one in Bluesky (Settings > Privacy and security > "
                                          "App passwords, tick direct messages) and enter it on the accounts page"}
        sess = outbox._bsky_session()
        return {"ok": True, "result": f"logged in as @{sess['handle']}"}
    return {"ok": False, "error": f"no check for {net}"}


def _download(media: list, folder: Path) -> list:
    out = []
    for i, m in enumerate(media):
        req = urllib.request.Request(m["url"], headers={"User-Agent": "v4air-publisher"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(MAX_MEDIA + 1)
            mime = r.headers.get_content_type()
        if len(data) > MAX_MEDIA:
            raise SystemExit(f"media {i + 1} is over 25 MB")
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4"}.get(mime, ".jpg")
        path = folder / f"media-{i + 1}{ext}"
        path.write_bytes(data)
        out.append({"path": str(path), "alt": m.get("alt") or "", "mime": mime})
    return out


def groups(_payload: dict) -> dict:
    from agent_reach.backends.opencli import opencli_call
    rows = opencli_call("facebook", ["groups", "--limit", "60"], timeout=120)
    rows = rows if isinstance(rows, list) else rows.get("data", []) if isinstance(rows, dict) else []
    return {"ok": True, "result": [{"name": r.get("name", ""), "url": r.get("url", "")} for r in rows
                                   if r.get("url", "").startswith("https://www.facebook.com/groups/")]}


def collect(payload: dict) -> dict:
    from . import collect as c
    return c.run(payload)


def _first_comment(post: dict, result: str) -> str:
    """Bluesky / X: the first comment is a reply to our own post, sent right after it."""
    from agent_reach import outbox
    url = result.split("posted: ", 1)[1].split()[0] if "posted: " in result else ""
    if post["kind"] == "twitter-post":
        import re
        m = re.search(r"https://(?:x|twitter)\.com/[^\s\"']+/status/\d+", result)
        url = m.group(0) if m else ""
    if not url.startswith("https://"):
        return " (first comment not sent: the post's link is unknown)"
    kind = "bluesky-reply" if post["kind"] == "bluesky-post" else "twitter-reply"
    d = {"kind": kind, "target": url, "expected_name": post["expected_name"], "text": post["options"]["first_comment"],
         "media": [], "status": "approved", "created": "", "result": ""}
    try:
        return " + first comment " + outbox.SENDERS[kind](d, True)
    except SystemExit as exc:
        return f" (first comment failed: {exc})"


def publish(payload: dict) -> dict:
    from agent_reach import outbox
    from agent_reach.runtime import pacing
    post = payload["post"]
    d = {**post, "status": "approved", "created": "", "result": ""}
    outbox.validate(d)
    if post["kind"].startswith("bluesky"):
        from agent_reach.config import Config
        cfg = Config(read_only=True)
        if not cfg.get("bluesky_handle") or not cfg.get("bluesky_app_password"):
            raise SystemExit("this Bluesky account has no app password yet: enter it on the accounts page")
    site = post["kind"].split("-")[0] + ":write"
    gap, extra, cap = outbox.WRITE_LIMITS[site]
    pacing.LIMITS.setdefault(site, (gap, extra, cap))
    with tempfile.TemporaryDirectory(dir=str(Path.home())) as tmp:
        d["media"] = _download(post.get("media") or [], Path(tmp))
        pacing.before(site)  # this account's write budget: a few a day, never a burst
        result = outbox.SENDERS[post["kind"]](d, True)
        if (post.get("options") or {}).get("first_comment") and post["kind"] in ("bluesky-post", "twitter-post"):
            result += _first_comment(post, result)
        return {"ok": True, "result": result}


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = json.loads(sys.stdin.read() or "{}")
    step = {"configure": configure, "check": check, "publish": publish, "groups": groups,
            "open": open_view, "close": close_browser, "collect": collect}.get(command)
    browser_step = network() not in ("bluesky", "") and command in ("check", "publish", "groups", "collect")
    try:
        if network() not in ("bluesky", ""):
            setup_home()
        out = step(payload) if step else {"ok": False, "error": f"unknown command {command!r}"}
    except SystemExit as exc:
        out = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - report, never crash the queue
        # an unexpected error during publish may come after the post went out
        prefix = "UNKNOWN: " if command == "publish" else ""
        out = {"ok": False, "error": f"{prefix}{type(exc).__name__}: {exc}"[:600]}
    finally:
        if browser_step:  # one browser at a time on the server: close it, keep its cookies
            try:
                close_browser()
            except Exception as exc:  # noqa: BLE001
                print(f"close: {exc}", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
