"""Fork: human-paced, low-volume use of the social sites. Every browser request to a site passes here.

Per site: a minimum gap between requests (plus a random extra, as a person reading would leave), a daily cap,
and a long pause after any sign that the site is limiting us (HTTP 429, "blocked", checkpoint pages). The pause
covers the whole site, so one blocked call cannot be followed by a burst of retries. State lives in
~/.agent-reach/pacing.json, shared by every agent process through a file lock.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import time
from contextlib import contextmanager

from .result import RATE_LIMITED, ReachError

#: site -> (minimum gap in s, random extra up to s, requests per day)
LIMITS = {
    "instagram": (20, 20, 80),
    "linkedin": (25, 20, 60),
    "facebook": (15, 15, 80),
    "reddit": (10, 10, 150),
}
DEFAULT = (8, 8, 200)
PAUSE_HOURS = 6
#: Lower-case fragments of an error that mean "the site is limiting this account or browser".
BLOCK_SIGNS = ("http 429", "too many requests", "rate limit", "blocked by network security", "checkpoint",
               "challenge_required", "please wait a few minutes", "try again later", "unusual activity")


def _path():
    from . import state
    return state.root() / "pacing.json"


def _enabled() -> bool:
    return os.environ.get("AGENT_REACH_PACING", "on") != "off"  # tests turn it off


@contextmanager
def _locked():
    import fcntl

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        except ValueError:
            data = {}
        yield data
        path.write_text(json.dumps(data, indent=1), "utf-8")


def before(site: str, *, now=time.time, sleep=time.sleep) -> None:
    """Wait for this site's next free slot, or refuse when paused or over today's cap."""
    if not _enabled():
        return
    gap, extra, cap = LIMITS.get(site, DEFAULT)
    with _locked() as data:
        rec = data.setdefault(site, {})
        t = now()
        if rec.get("paused_until", 0) > t:
            until = dt.datetime.fromtimestamp(rec["paused_until"]).strftime("%H:%M")
            raise ReachError(RATE_LIMITED, f"{site} is paused until {until} after signs that the site is limiting this "
                             f"account ({rec.get('pause_reason', '')[:80]})",
                             hint="Do not retry or work around it. Tell the user when it can be tried again.")
        today = dt.date.fromtimestamp(t).isoformat()
        if rec.get("day") != today:
            rec.update(day=today, count=0)
        if rec["count"] >= cap:
            raise ReachError(RATE_LIMITED, f"{site}: today's budget of {cap} requests is used up",
                             hint="Stop for today; this keeps use at a human level.")
        start = max(t, rec.get("next_at", 0))
        rec["next_at"] = start + gap + random.uniform(0, extra)  # reserve our slot before releasing the lock
        rec["count"] += 1
    if start > t:
        sleep(start - t)


def failed(site: str, message: str, *, now=time.time) -> bool:
    """Pause the whole site when an error looks like rate limiting or a block. Returns True if paused."""
    if not _enabled() or not any(sign in (message or "").lower() for sign in BLOCK_SIGNS):
        return False
    with _locked() as data:
        rec = data.setdefault(site, {})
        rec["paused_until"] = now() + PAUSE_HOURS * 3600
        rec["pause_reason"] = (message or "")[:200]
    return True


def status() -> dict:
    with _locked() as data:
        return json.loads(json.dumps(data))
