"""SQLite store of the publisher: accounts, posts (the queue) and an append-only log."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, network TEXT NOT NULL, label TEXT NOT NULL, owner TEXT NOT NULL, route TEXT NOT NULL,
  handle TEXT NOT NULL DEFAULT '', proxy TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL REFERENCES accounts(id), payload TEXT NOT NULL,
  hash TEXT NOT NULL, author TEXT NOT NULL, due REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  result TEXT NOT NULL DEFAULT '', created REAL NOT NULL, finished REAL);
CREATE TABLE IF NOT EXISTS log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inbox (
  id TEXT PRIMARY KEY, account TEXT NOT NULL, network TEXT NOT NULL, type TEXT NOT NULL, post_url TEXT NOT NULL DEFAULT '',
  item_url TEXT NOT NULL DEFAULT '', author_handle TEXT NOT NULL DEFAULT '', author_name TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL DEFAULT '', question INTEGER NOT NULL DEFAULT 0, created_at REAL, fetched_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'new', answered_by TEXT NOT NULL DEFAULT '', answered_at REAL, reply_post INTEGER);
CREATE INDEX IF NOT EXISTS inbox_by_time ON inbox(fetched_at);
CREATE TABLE IF NOT EXISTS own_posts (
  url TEXT PRIMARY KEY, account TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', created_at REAL, post_id INTEGER,
  first_seen REAL NOT NULL, last TEXT NOT NULL DEFAULT '{}', last_at REAL);
CREATE TABLE IF NOT EXISTS post_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, account TEXT NOT NULL, at REAL NOT NULL, likes INTEGER,
  reposts INTEGER, replies INTEGER, quotes INTEGER, views INTEGER, impressions INTEGER, saves INTEGER);
CREATE INDEX IF NOT EXISTS post_metrics_by_url ON post_metrics(url, at);
CREATE TABLE IF NOT EXISTS profile_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL, at REAL NOT NULL, followers INTEGER, following INTEGER,
  posts INTEGER, profile_views INTEGER, impressions INTEGER, connections INTEGER);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS saved_replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, text TEXT NOT NULL, author TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS collect_state (
  account TEXT PRIMARY KEY, last_at REAL, next_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
  manual_at REAL);
"""
#: columns added after the first release: (table, column, definition)
MIGRATIONS = [("posts", "meta", "TEXT NOT NULL DEFAULT '{}'"), ("posts", "url", "TEXT NOT NULL DEFAULT ''")]


def data_dir() -> Path:
    d = Path(os.environ.get("PUBLISHER_DATA", Path.home() / ".agent-reach-publisher"))
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()  # Agent Reach refuses config paths through symlinks (macOS /tmp, some volume mounts)


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(data_dir() / "publisher.db", timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    for table, column, definition in MIGRATIONS:
        if column not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return db


def log(db, actor: str, event: str, detail) -> None:
    db.execute("INSERT INTO log(at, actor, event, detail) VALUES (?,?,?,?)",
               (time.time(), actor, event, json.dumps(detail, ensure_ascii=False)))


def rows(cursor) -> list:
    return [dict(r) for r in cursor]
