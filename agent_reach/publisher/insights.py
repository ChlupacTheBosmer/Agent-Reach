"""Inbox, metrics, drafts, saved replies and the calendar query, on top of the publisher store.

Collection runs the read-only `collect` job in each account's home (collect.py) on a schedule inside the day window
and well under each site's pacing budget; the scheduler collects at most one account per round, after publishing.
"""
from __future__ import annotations

import csv
import io
import json
import random
import statistics
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import core, store

#: seconds between polls per network (±20 % jitter); Bluesky is the official API, the rest are paced browser reads
INTERVAL = {"bluesky": 300, "twitter": 3600, "facebookpage": 7200, "facebook": 10800, "instagram": 10800,
            "linkedin": 10800, "linkedinpage": 10800}
WINDOW = (7, 22)  # Prague hours when browser accounts are read
MANUAL_GAP = 900
SNAPSHOT_GAP = 6 * 3600  # a new metrics snapshot when counters changed, or at least this often


def _in_window(now: float, network: str) -> bool:
    if network == "bluesky":
        return True
    h = datetime.fromtimestamp(now, ZoneInfo("Europe/Prague")).hour
    return WINDOW[0] <= h < WINDOW[1]


# ── collection ──

# "Fetch now": a person asks for fresh comments; the accounts are read one after another in the background (browser
# accounts take turns with one browser, 20-120 s each); the app asks fetch_status() for progress.
_fetch = {"running": False, "queue": [], "current": None, "done": [], "started": 0.0, "actor": ""}
_fetch_lock = threading.Lock()


def fetch_now(accounts: list, actor: str) -> dict:
    ids = [str(a) for a in accounts if a][:20]
    with _fetch_lock:
        if _fetch["running"]:
            _fetch["queue"] += [a for a in ids if a not in _fetch["queue"] and a != _fetch["current"]]
            return fetch_status()
        _fetch.update(running=True, queue=ids, current=None, done=[], started=time.time(), actor=actor)

    def work():
        db = store.connect()
        try:
            while True:
                with _fetch_lock:
                    if not _fetch["queue"]:
                        _fetch.update(running=False, current=None)
                        return
                    acc = _fetch["current"] = _fetch["queue"].pop(0)
                try:
                    out = collect_account(db, acc, actor, manual=True)
                except core.Refused as exc:
                    out = {"ok": False, "error": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    out = {"ok": False, "error": friendly(str(exc))}
                with _fetch_lock:
                    _fetch["done"].append({"account": acc, **out})
        finally:
            with _fetch_lock:
                _fetch.update(running=False, current=None)
            db.close()

    threading.Thread(target=work, daemon=True).start()
    return fetch_status()


def fetch_status() -> dict:
    with _fetch_lock:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _fetch.items() if k != "actor"}


def collect_account(db, account_id: str, actor: str = "publisher", manual: bool = False) -> dict:
    acc = core.account(db, account_id)
    now = time.time()
    st = store.rows(db.execute("SELECT * FROM collect_state WHERE account=?", (account_id,)))
    st = st[0] if st else {}
    if manual and st.get("manual_at") and now - st["manual_at"] < MANUAL_GAP:
        raise core.Refused("refreshed a moment ago; try again in a few minutes (sites are read at a human pace)")
    known = {r["url"]: json.loads(r["last"]).get("replies") or 0 for r in store.rows(
        db.execute("SELECT url, last FROM own_posts WHERE account=?", (account_id,)))}
    core._ACCOUNTS_CACHE[account_id] = acc["handle"]
    out = core.in_home(account_id, "collect", {"account": {k: acc[k] for k in ("id", "network", "label", "handle")},
                                               "known": known}, timeout=1500)
    gap = INTERVAL.get(acc["network"], 10800) * random.uniform(0.8, 1.2)
    if not out.get("ok"):
        err = friendly(str(out.get("error") or "collection failed"))
        db.execute("INSERT INTO collect_state(account, last_at, next_at, last_error, manual_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(account) DO UPDATE SET last_at=excluded.last_at, next_at=excluded.next_at, "
                   "last_error=excluded.last_error, manual_at=COALESCE(excluded.manual_at, manual_at)",
                   (account_id, now, now + gap * 2, err, now if manual else None))
        store.log(db, actor, "collect.failed", {"account": account_id, "error": err})
        return {"ok": False, "error": err}
    res = out["result"]
    new = _save(db, acc, res, now)
    db.execute("INSERT INTO collect_state(account, last_at, next_at, last_error, manual_at) VALUES (?,?,?,'',?) "
               "ON CONFLICT(account) DO UPDATE SET last_at=excluded.last_at, next_at=excluded.next_at, last_error='', "
               "manual_at=COALESCE(excluded.manual_at, manual_at)", (account_id, now, now + gap, now if manual else None))
    store.log(db, actor, "collect", {"account": account_id, "new_inbox": new, "posts": len(res.get("posts", []))})
    return {"ok": True, "result": f"{new} new in the inbox, {len(res.get('posts', []))} posts measured"}


def _save(db, acc: dict, res: dict, now: float) -> int:
    new = 0
    for it in res.get("inbox", []):
        cur = db.execute("INSERT OR IGNORE INTO inbox(id, account, network, type, post_url, item_url, author_handle, author_name, "
                         "text, question, created_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (it["id"], acc["id"], acc["network"], it.get("type", "comment"), it.get("post_url", ""), it.get("item_url", ""),
                          it.get("author_handle", ""), it.get("author_name", ""), it.get("text", ""), int(bool(it.get("question"))),
                          it.get("created_at"), now))
        new += cur.rowcount
    keys = ("likes", "reposts", "replies", "quotes", "views", "impressions", "saves")
    for p in res.get("posts", []):
        if not p.get("url"):
            continue
        counters = {k: p.get(k) for k in keys}
        row = store.rows(db.execute("SELECT last, last_at FROM own_posts WHERE url=?", (p["url"],)))
        if not row:
            db.execute("INSERT INTO own_posts(url, account, text, created_at, first_seen, last, last_at) VALUES (?,?,?,?,?,?,?)",
                       (p["url"], acc["id"], p.get("text", "")[:2000], p.get("created_at"), now, json.dumps(counters), now))
            changed = True
        else:
            changed = json.loads(row[0]["last"]) != counters or now - (row[0]["last_at"] or 0) > SNAPSHOT_GAP
            if changed:
                db.execute("UPDATE own_posts SET last=?, last_at=?, text=CASE WHEN text='' THEN ? ELSE text END WHERE url=?",
                           (json.dumps(counters), now, p.get("text", "")[:2000], p["url"]))
        if changed:
            db.execute("INSERT INTO post_metrics(url, account, at, likes, reposts, replies, quotes, views, impressions, saves) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)", (p["url"], acc["id"], now, *[counters[k] for k in keys]))
    prof = res.get("profile") or {}
    if any(v is not None for v in prof.values()):
        db.execute("INSERT INTO profile_metrics(account, at, followers, following, posts, profile_views, impressions, connections) "
                   "VALUES (?,?,?,?,?,?,?,?)", (acc["id"], now, *[prof.get(k) for k in
                                                                  ("followers", "following", "posts", "profile_views", "impressions", "connections")]))
    return new


def friendly(err: str) -> str:
    """Short reasons a team member can act on, instead of tool output."""
    e = err.lower()
    for needle, text in (("app password", "needs its app password: Accounts › Credentials"),
                         ("browser did not open", "its browser could not start; try again later"),
                         ("not logged in", "not logged in: Accounts › Log in"), ("login", "needs a login: Accounts › Log in"),
                         ("rate_limited", "paused for now (the site asked to slow down); it resumes on its own"),
                         ("budget", "today's reading budget is used up; it resumes tomorrow"),
                         ("checkpoint", "the site wants a person to confirm something: Accounts › Log in"),
                         ("timed out", "the site did not answer in time; it tries again later")):
        if needle in e:
            return text
    return err.split("\n")[0][:160]


def collect_due(db, now: float | None = None) -> dict | None:
    """Collect the one account most overdue (logged-in accounts only, inside the day window)."""
    now = time.time() if now is None else now
    for acc in store.rows(db.execute("SELECT a.* FROM accounts a LEFT JOIN collect_state c ON c.account=a.id "
                                     "WHERE a.status='ok' ORDER BY COALESCE(c.next_at, 0)")):
        st = store.rows(db.execute("SELECT next_at FROM collect_state WHERE account=?", (acc["id"],)))
        if (st and st[0]["next_at"] > now) or not _in_window(now, acc["network"]):
            continue
        return collect_account(db, acc["id"])
    return None


def mark_sent(db, post: dict, result: str) -> None:
    """After a post went out: remember its link, measure it from now on, close the inbox item it answered."""
    url = result.split("posted: ", 1)[1].split()[0] if "posted: " in result else ""
    url = url if url.startswith("https://") else ""
    meta = json.loads(post.get("meta") or "{}")
    if url:
        db.execute("UPDATE posts SET url=? WHERE id=?", (url, post["id"]))
        db.execute("INSERT OR IGNORE INTO own_posts(url, account, text, created_at, post_id, first_seen) VALUES (?,?,?,?,?,?)",
                   (url, post["account"], post["payload"].get("text", "")[:2000], time.time(), post["id"], time.time()))
        db.execute("UPDATE own_posts SET post_id=? WHERE url=?", (post["id"], url))
    if meta.get("inbox"):
        db.execute("UPDATE inbox SET status='answered', answered_by=?, answered_at=?, reply_post=? WHERE id=?",
                   (post["author"], time.time(), post["id"], meta["inbox"]))


# ── inbox ──

def inbox_query(db, f: dict) -> list:
    sql, args = ["SELECT i.*, a.label AS account_label FROM inbox i JOIN accounts a ON a.id=i.account WHERE 1=1"], []
    for key in ("id", "network", "account", "status", "type"):
        vals = f.get(key)
        if isinstance(vals, list) and not vals and key == "account":
            return []  # a member with no usable accounts sees nothing, not everything
        if vals:
            vals = vals if isinstance(vals, list) else [vals]
            sql.append(f"AND i.{key} IN ({','.join('?' * len(vals))})")
            args += vals
    if f.get("question"):
        sql.append("AND i.question=1")
    if f.get("search"):
        sql.append("AND (i.text LIKE ? OR i.author_name LIKE ? OR i.author_handle LIKE ?)")
        args += [f"%{f['search']}%"] * 3
    sql.append("ORDER BY COALESCE(i.created_at, i.fetched_at) DESC LIMIT 300")
    return store.rows(db.execute(" ".join(sql), args))


def inbox_update(db, item_id: str, body: dict, actor: str) -> dict:
    status = body.get("status")
    if status not in ("new", "done", "ignored", "answered"):
        raise core.Refused("status must be new, done, ignored or answered")
    if not db.execute("UPDATE inbox SET status=?, answered_by=CASE WHEN ?='answered' THEN ? ELSE answered_by END WHERE id=?",
                      (status, status, actor, item_id)).rowcount:
        raise core.Refused("no such inbox item")
    store.log(db, actor, "inbox.status", {"id": item_id, "status": status})
    return {"ok": True}


def collect_status(db) -> list:
    return store.rows(db.execute("SELECT c.*, a.label, a.network FROM collect_state c JOIN accounts a ON a.id=c.account"))


# ── metrics ──

ENGAGE = ("likes", "reposts", "replies", "quotes", "saves")


def _engagement(c: dict) -> int:
    return sum(int(c.get(k) or 0) for k in ENGAGE)


def metrics(db, f: dict) -> dict:
    since = time.time() - float(f.get("days") or 90) * 86400
    allowed = f.get("accounts")  # the Worker limits a member to the accounts they may use
    accounts = {a["id"]: a for a in store.rows(db.execute("SELECT id, network, label, handle FROM accounts")) if allowed is None or a["id"] in allowed}
    out_accounts = []
    for aid, a in accounts.items():
        series = store.rows(db.execute("SELECT at, followers, following, posts, profile_views, impressions FROM profile_metrics "
                                       "WHERE account=? AND at>=? ORDER BY at", (aid, since)))
        last = series[-1] if series else {}
        first = series[0] if series else {}
        out_accounts.append({**a, "followers": last.get("followers"), "following": last.get("following"),
                             "profile_views": last.get("profile_views"), "impressions": last.get("impressions"),
                             "followers_change": (last.get("followers") or 0) - (first.get("followers") or 0) if series else None,
                             "series": [[r["at"], r["followers"]] for r in series if r["followers"] is not None]})
    followers = {a["id"]: a["followers"] for a in out_accounts}
    posts = []
    for p in store.rows(db.execute("SELECT o.*, ps.author, ps.meta FROM own_posts o LEFT JOIN posts ps ON ps.id=o.post_id "
                                   "WHERE COALESCE(o.created_at, o.first_seen)>=? ORDER BY COALESCE(o.created_at, o.first_seen) DESC", (since,))):
        if p["account"] not in accounts:
            continue
        c = json.loads(p["last"] or "{}")
        eng = _engagement(c)
        reach = c.get("impressions") or c.get("views")
        base = reach or followers.get(p["account"])
        hist = store.rows(db.execute("SELECT at, likes, reposts, replies, quotes, views, impressions, saves FROM post_metrics "
                                     "WHERE url=? ORDER BY at", (p["url"],)))
        meta = json.loads(p.get("meta") or "{}")
        posts.append({"url": p["url"], "account": p["account"], "network": accounts.get(p["account"], {}).get("network"),
                      "account_label": accounts.get(p["account"], {}).get("label"), "text": p["text"], "created_at": p["created_at"] or p["first_seen"],
                      **c, "engagement": eng, "engagement_rate": round(eng / base, 4) if base else None,
                      "rate_basis": "impressions" if reach else "followers" if base else None,
                      "via_app": p["post_id"] is not None, "author": p.get("author"), "tags": meta.get("tags", []),
                      "history": [[h["at"], _engagement(h), h.get("impressions") or h.get("views")] for h in hist]})
    items = [i for i in store.rows(db.execute("SELECT account, network, status, question, created_at, fetched_at, answered_at FROM inbox WHERE fetched_at>=?", (since,)))
             if i["account"] in accounts]
    answered = [i for i in items if i["status"] == "answered" and i["answered_at"]]
    response = [(i["answered_at"] - (i["created_at"] or i["fetched_at"])) / 60 for i in answered]
    by_net = {}
    for i in items:
        n = by_net.setdefault(i["network"], {"total": 0, "answered": 0, "open": 0})
        n["total"] += 1
        n["answered"] += i["status"] == "answered"
        n["open"] += i["status"] == "new"
    comments = {"total": len(items), "answered": len(answered), "open": sum(i["status"] == "new" for i in items),
                "open_questions": sum(i["status"] == "new" and i["question"] for i in items),
                "reply_rate": round(len(answered) / len(items), 3) if items else None,
                "median_response_minutes": round(statistics.median(response)) if response else None, "by_network": by_net}
    by_tag = {}
    for p in posts:
        for t in p["tags"] or ["(no tag)"]:
            g = by_tag.setdefault(t, {"posts": 0, "engagement": 0})
            g["posts"] += 1
            g["engagement"] += p["engagement"]
    return {"accounts": out_accounts, "posts": posts, "comments": comments, "tags": by_tag, "since": since}


def metrics_csv(db, f: dict) -> dict:
    m = metrics(db, f)
    buf = io.StringIO()
    cols = ["created_at", "network", "account_label", "url", "text", "likes", "reposts", "replies", "quotes", "saves",
            "views", "impressions", "engagement", "engagement_rate", "rate_basis", "via_app", "author", "tags"]
    w = csv.writer(buf)
    w.writerow(cols)
    for p in m["posts"]:
        w.writerow([datetime.fromtimestamp(p["created_at"]).isoformat(timespec="minutes") if p.get("created_at") else ""] +
                   [(" ".join(p[c]) if c == "tags" else p.get(c, "")) for c in cols[1:]])
    return {"csv": buf.getvalue(), "filename": f"v4air-posts-{datetime.now():%Y-%m-%d}.csv"}


# ── drafts, saved replies, calendar ──

def drafts(db) -> list:
    return [{**d, "payload": json.loads(d["payload"])} for d in store.rows(db.execute("SELECT * FROM drafts ORDER BY updated DESC LIMIT 200"))]


def save_draft(db, body: dict, actor: str) -> dict:
    now, payload = time.time(), json.dumps(body.get("payload") or {}, ensure_ascii=False)
    title = (body.get("title") or "").strip()[:120] or "Untitled draft"
    if body.get("id"):
        if not db.execute("UPDATE drafts SET title=?, payload=?, updated=? WHERE id=?", (title, payload, now, int(body["id"]))).rowcount:
            raise core.Refused("no such draft")
        return {"id": int(body["id"])}
    cur = db.execute("INSERT INTO drafts(author, title, payload, created, updated) VALUES (?,?,?,?,?)", (actor, title, payload, now, now))
    return {"id": cur.lastrowid}


def delete_draft(db, draft_id: int) -> dict:
    db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    return {"ok": True}


def replies(db) -> list:
    return store.rows(db.execute("SELECT * FROM saved_replies ORDER BY title"))


def save_reply(db, body: dict, actor: str) -> dict:
    title, text = (body.get("title") or "").strip()[:80], (body.get("text") or "").strip()[:2000]
    if not title or not text:
        raise core.Refused("a saved reply needs a title and a text")
    if body.get("id"):
        db.execute("UPDATE saved_replies SET title=?, text=? WHERE id=?", (title, text, int(body["id"])))
        return {"id": int(body["id"])}
    return {"id": db.execute("INSERT INTO saved_replies(title, text, author, created) VALUES (?,?,?,?)",
                             (title, text, actor, time.time())).lastrowid}


def delete_reply(db, reply_id: int) -> dict:
    db.execute("DELETE FROM saved_replies WHERE id=?", (reply_id,))
    return {"ok": True}


def posts_query(db, f: dict) -> list:
    lo, hi = float(f.get("from") or 0), float(f.get("to") or 4e9)
    return [{**r, "payload": json.loads(r["payload"]), "meta": json.loads(r.get("meta") or "{}")} for r in store.rows(
        db.execute("SELECT * FROM posts WHERE due>=? AND due<? ORDER BY due LIMIT 500", (lo, hi)))]
