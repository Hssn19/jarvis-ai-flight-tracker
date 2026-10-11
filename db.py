import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH", "jarvis.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc)


def init_db():
    with closing(_conn()) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                date TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                cheapest REAL,
                payload TEXT NOT NULL
            )"""
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_route "
            "ON searches(origin, destination, date, fetched_at)"
        )
        c.commit()


def get_cached(origin: str, destination: str, date: str, ttl_minutes: int = 30):
    cutoff = (_now() - timedelta(minutes=ttl_minutes)).isoformat()
    with closing(_conn()) as c:
        row = c.execute(
            "SELECT fetched_at, payload FROM searches "
            "WHERE origin=? AND destination=? AND date=? AND fetched_at>=? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (origin, destination, date, cutoff),
        ).fetchone()
    if not row:
        return None
    return {"fetched_at": row["fetched_at"], "payload": json.loads(row["payload"])}


def save_search(origin: str, destination: str, date: str, payload: dict, cheapest):
    with closing(_conn()) as c:
        c.execute(
            "INSERT INTO searches (origin, destination, date, fetched_at, cheapest, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (origin, destination, date, _now().isoformat(), cheapest, json.dumps(payload)),
        )
        c.commit()


def get_history(origin: str, destination: str, date: str, limit: int = 100):
    with closing(_conn()) as c:
        rows = c.execute(
            "SELECT fetched_at, cheapest FROM searches "
            "WHERE origin=? AND destination=? AND date=? AND cheapest IS NOT NULL "
            "ORDER BY fetched_at DESC LIMIT ?",
            (origin, destination, date, limit),
        ).fetchall()
    return [{"fetched_at": r["fetched_at"], "cheapest": r["cheapest"]} for r in reversed(rows)]