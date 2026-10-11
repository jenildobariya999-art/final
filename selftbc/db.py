"""SQLite storage for SelfTBC."""
import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("SELFTBC_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "selftbc.db"))

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots(
  id TEXT PRIMARY KEY, token TEXT NOT NULL, name TEXT, username TEXT,
  running INTEGER DEFAULT 0, version TEXT DEFAULT '1.0.0',
  tg_offset INTEGER DEFAULT 0, created REAL, started_at REAL, extra TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS commands(
  bot_id TEXT, name TEXT, code TEXT, updated REAL, runs INTEGER DEFAULT 0,
  PRIMARY KEY(bot_id, name));
CREATE TABLE IF NOT EXISTS users(
  bot_id TEXT, user_id TEXT, first_name TEXT, username TEXT, lang TEXT,
  created REAL, last_active REAL, blocked INTEGER DEFAULT 0,
  PRIMARY KEY(bot_id, user_id));
CREATE TABLE IF NOT EXISTS udata(
  bot_id TEXT, user_id TEXT, name TEXT, value TEXT,
  PRIMARY KEY(bot_id, user_id, name));
CREATE TABLE IF NOT EXISTS bdata(
  bot_id TEXT, name TEXT, value TEXT, PRIMARY KEY(bot_id, name));
CREATE TABLE IF NOT EXISTS waits(
  bot_id TEXT, user_id TEXT, command TEXT, options TEXT, cancel_at_command INTEGER DEFAULT 0,
  PRIMARY KEY(bot_id, user_id));
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, bot_id TEXT, user_id TEXT, chat_id TEXT, command TEXT, options TEXT, run_at REAL);
CREATE INDEX IF NOT EXISTS jobs_run ON jobs(run_at);
CREATE TABLE IF NOT EXISTS errors(
  id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, ts REAL, command TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS broadcasts(
  id TEXT PRIMARY KEY, bot_id TEXT, command TEXT, status TEXT, total INTEGER,
  sent INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, speed INTEGER DEFAULT 8, started REAL, finished REAL);
"""


def conn():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
    return _conn


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        return cur.rowcount


def xmany(sql, rows):
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            c.executemany(sql, rows)
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def dumps(v):
    return json.dumps(v, ensure_ascii=False, default=str)


def loads(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return s


def now():
    return time.time()
