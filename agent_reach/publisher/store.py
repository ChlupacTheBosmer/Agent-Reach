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
"""


def data_dir() -> Path:
    d = Path(os.environ.get("PUBLISHER_DATA", Path.home() / ".agent-reach-publisher"))
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()  # Agent Reach refuses config paths through symlinks (macOS /tmp, some volume mounts)


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(data_dir() / "publisher.db", timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def log(db, actor: str, event: str, detail) -> None:
    db.execute("INSERT INTO log(at, actor, event, detail) VALUES (?,?,?,?)",
               (time.time(), actor, event, json.dumps(detail, ensure_ascii=False)))


def rows(cursor) -> list:
    return [dict(r) for r in cursor]
