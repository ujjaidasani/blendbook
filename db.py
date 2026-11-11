"""
Blendbook's own database: everything Blendbook observes itself (controller mix log, goals, set plans,
recording analysis). Separate from rekordbox's master.db, which is only ever read.

Schema changes go in MIGRATIONS as new entries; never edit an applied one.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from pathlib import Path

import paths

DB_PATH = Path(os.environ.get("BLENDBOOK_DB") or paths.DATA_DIR / "blendbook.db")

MIGRATIONS = [
    # 1: phase A groundwork
    """
    CREATE TABLE mix_events (
        id INTEGER PRIMARY KEY,
        uid TEXT NOT NULL UNIQUE,          -- client-generated, makes retries idempotent
        client_session TEXT NOT NULL,      -- one per controller connection
        ts INTEGER NOT NULL,               -- ms since epoch
        type TEXT NOT NULL,
        deck INTEGER,
        payload TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX mix_events_ts ON mix_events(ts);

    CREATE TABLE mix_transitions (
        id INTEGER PRIMARY KEY,
        event_uid TEXT NOT NULL UNIQUE REFERENCES mix_events(uid),
        client_session TEXT NOT NULL,
        started_ts INTEGER NOT NULL,
        ended_ts INTEGER NOT NULL,
        from_deck INTEGER,
        to_deck INTEGER,
        duration_sec REAL,
        bass_overlap_sec REAL,
        eq_moves INTEGER,
        verdict TEXT
    );
    CREATE INDEX mix_transitions_started ON mix_transitions(started_ts);

    CREATE TABLE goals (
        id INTEGER PRIMARY KEY,
        metric TEXT NOT NULL,
        target REAL NOT NULL,
        direction TEXT NOT NULL DEFAULT 'at_most',
        focus INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        archived_at TEXT
    );

    CREATE TABLE set_plans (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        track_ids TEXT NOT NULL,           -- JSON array of rekordbox content ids
        shape TEXT,                        -- JSON energy curve
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        exported_playlist_id TEXT
    );

    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL,
        sha1 TEXT NOT NULL UNIQUE,
        session_id TEXT,
        offset_sec REAL,
        features_path TEXT,
        analysed_at TEXT
    );
    """,
    # 2: phase 2, recordings carry their own timing so they can be matched to sets at read time
    """
    ALTER TABLE recordings ADD COLUMN kind TEXT NOT NULL DEFAULT 'file';   -- 'file' or 'mic'
    ALTER TABLE recordings ADD COLUMN started_at TEXT;
    ALTER TABLE recordings ADD COLUMN duration_sec REAL;
    ALTER TABLE recordings ADD COLUMN error TEXT;
    """,
    # 3: online catalogs (online.py): response cache, library tracks matched online, catalog tracks used in plans
    """
    CREATE TABLE online_cache (
        key TEXT PRIMARY KEY,              -- request URL
        value TEXT NOT NULL,               -- JSON response
        fetched_at INTEGER NOT NULL        -- unix seconds
    );
    CREATE TABLE track_online (
        track_id TEXT PRIMARY KEY,         -- rekordbox content id
        status TEXT NOT NULL,              -- 'matched' | 'not_found' | 'error'
        recco_id TEXT,
        spotify_url TEXT,
        bpm REAL,
        key TEXT,                          -- Camelot, e.g. 8A
        energy REAL,
        danceability REAL,
        valence REAL,
        checked_at INTEGER NOT NULL
    );
    CREATE TABLE online_tracks (
        id TEXT PRIMARY KEY,               -- 'web:<ReccoBeats id>'
        data TEXT NOT NULL,                -- JSON: title, artist, bpm, key, energy, length, links
        seen_at INTEGER NOT NULL
    );
    """,
    # 4: catalog tracks now come from Apple. Plans keep their old 'web:dz…' ids, which are only labels; what pointed
    # at Deezer goes
    """
    UPDATE online_tracks SET data = json_remove(data, '$.deezer_id', '$.deezer_url') WHERE json_valid(data);
    DELETE FROM online_cache WHERE key LIKE 'https://api.deezer.com/%';
    """,
]

_lock = threading.Lock()
MAX_EVENT_PAYLOAD = 4000  # bytes of JSON per controller event


def _number(v, lo: float, hi: float) -> float | None:
    """A finite number within [lo, hi], else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v) if lo <= v <= hi else None


def _deck(v) -> int | None:
    n = _number(v, 1, 4)
    return int(n) if n is not None and n == int(n) else None


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    version = row[0] if row else 0
    if not row:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(sql)
            conn.execute("UPDATE schema_version SET version = ?", (i,))
    conn.commit()
    return len(MIGRATIONS)


class Store:
    """Shared by the HTTP server's request threads: each thread gets its own SQLite connection (WAL lets them
    read concurrently), and writes are serialised with _lock."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or DB_PATH)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()  # separate from _lock, which callers may already hold
        _ = self.conn  # create the file and run migrations up front

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = connect(self.path)
            self._local.conn = c
            with self._conns_lock:
                self._all.append(c)
        return c

    def close(self) -> None:
        with self._conns_lock:
            for c in self._all:
                c.close()
            self._all.clear()
        self._local = threading.local()

    # ---------- mix log ----------

    def add_mix_events(self, client_session: str, events: list[dict]) -> int:
        """Store controller events. Duplicate uids (client retries) are ignored. Returns rows added.

        Events come from the browser, so every field is coerced to the type its column expects and junk is dropped."""
        added = 0
        with _lock, self.conn:
            for e in events:
                uid = e.get("uid")
                etype = e.get("type")
                ts = _number(e.get("ts"), 0, 4.1e12)  # ms since epoch, before 2100
                if not isinstance(uid, str) or not uid or len(uid) > 64 or not isinstance(etype, str) or not etype \
                        or len(etype) > 40 or ts is None:
                    continue
                payload = json.dumps({k: v for k, v in e.items() if k not in ("uid", "ts", "type", "deck")})
                if len(payload) > MAX_EVENT_PAYLOAD:
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO mix_events (uid, client_session, ts, type, deck, payload) VALUES (?,?,?,?,?,?)",
                    (uid, client_session, int(ts), etype, _deck(e.get("deck")), payload),
                )
                if not cur.rowcount:
                    continue
                added += 1
                if etype == "transition_end":
                    dur = _number(e.get("durationSec"), 0, 3600) or 0.0
                    eq = _number(e.get("eqMoves"), 0, 100_000)
                    verdict = e.get("verdict")
                    self.conn.execute(
                        """INSERT OR IGNORE INTO mix_transitions
                           (event_uid, client_session, started_ts, ended_ts, from_deck, to_deck,
                            duration_sec, bass_overlap_sec, eq_moves, verdict)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (uid, client_session, int(ts - dur * 1000), int(ts), _deck(e.get("from")), _deck(e.get("to")),
                         dur, _number(e.get("bassOverlapSec"), 0, 3600), int(eq) if eq is not None else None,
                         verdict[:200] if isinstance(verdict, str) else None),
                    )
        return added

    def mix_transitions_between(self, start_ms: int, end_ms: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM mix_transitions WHERE started_ts BETWEEN ? AND ? ORDER BY started_ts",
            (start_ms, end_ms),
        ).fetchall()
        return [dict(r) for r in rows]

    def mix_events_between(self, start_ms: int, end_ms: int, types: tuple[str, ...] | None = None) -> list[dict]:
        sql = "SELECT * FROM mix_events WHERE ts BETWEEN ? AND ?"
        args: list = [start_ms, end_ms]
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            args += list(types)
        rows = self.conn.execute(sql + " ORDER BY ts", args).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def mix_log_status(self) -> dict:
        row = self.conn.execute("SELECT COUNT(*) n, MAX(ts) last FROM mix_events").fetchone()
        t = self.conn.execute("SELECT COUNT(*) FROM mix_transitions").fetchone()[0]
        return {"events": row["n"], "transitions": t, "last_event_ts": row["last"]}

    # ---------- goals / plans / recordings (used by later phases) ----------

    def add_goal(self, metric: str, target: float, direction: str = "at_most", focus: bool = False) -> int:
        with _lock, self.conn:
            if focus:
                self.conn.execute("UPDATE goals SET focus = 0")
            return self.conn.execute(
                "INSERT INTO goals (metric, target, direction, focus) VALUES (?,?,?,?)",
                (metric, target, direction, int(focus)),
            ).lastrowid

    def archive_goal(self, goal_id: int) -> bool:
        with _lock, self.conn:
            return bool(self.conn.execute(
                "UPDATE goals SET archived_at = datetime('now'), focus = 0 WHERE id = ? AND archived_at IS NULL",
                (goal_id,)).rowcount)

    def focus_goal(self, goal_id: int) -> bool:
        with _lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM goals WHERE id = ? AND archived_at IS NULL", (goal_id,)).fetchone():
                return False
            self.conn.execute("UPDATE goals SET focus = (id = ?)", (goal_id,))
            return True

    def goals(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM goals WHERE archived_at IS NULL ORDER BY id")]

    def add_set_plan(self, name: str, track_ids: list[str], shape=None) -> int:
        with _lock, self.conn:
            return self.conn.execute(
                "INSERT INTO set_plans (name, track_ids, shape) VALUES (?,?,?)",
                (name, json.dumps(track_ids), json.dumps(shape) if shape is not None else None),
            ).lastrowid

    def set_plan(self, plan_id: int) -> dict | None:
        return next((p for p in self.set_plans() if p["id"] == plan_id), None)

    def mark_plan_exported(self, plan_id: int, playlist_id: str | None) -> None:
        with _lock, self.conn:
            self.conn.execute("UPDATE set_plans SET exported_playlist_id = ? WHERE id = ?", (playlist_id, plan_id))

    def delete_set_plan(self, plan_id: int) -> bool:
        with _lock, self.conn:
            return bool(self.conn.execute("DELETE FROM set_plans WHERE id = ?", (plan_id,)).rowcount)

    def set_plans(self) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT * FROM set_plans ORDER BY id DESC"):
            d = dict(r)
            d["track_ids"] = json.loads(d["track_ids"])
            d["shape"] = json.loads(d["shape"]) if d["shape"] else None
            out.append(d)
        return out

    def add_recording(self, path: str, sha1: str, **fields) -> int:
        cols = ["path", "sha1", *fields]
        with _lock, self.conn:
            return self.conn.execute(
                f"INSERT INTO recordings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                (path, sha1, *fields.values()),
            ).lastrowid

    def recordings(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM recordings ORDER BY id")]

    def recording(self, rec_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM recordings WHERE id = ?", (rec_id,)).fetchone()
        return dict(r) if r else None

    def recording_by(self, column: str, value) -> dict | None:
        assert column in ("path", "sha1")
        r = self.conn.execute(f"SELECT * FROM recordings WHERE {column} = ?", (value,)).fetchone()
        return dict(r) if r else None

    def update_recording(self, rec_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with _lock, self.conn:
            self.conn.execute(f"UPDATE recordings SET {cols} WHERE id = ?", (*fields.values(), rec_id))

    # ---------- online catalogs ----------

    def cache_get(self, key: str, max_age_sec: int):
        r = self.conn.execute("SELECT value, fetched_at FROM online_cache WHERE key = ?", (key,)).fetchone()
        if r and time.time() - r["fetched_at"] <= max_age_sec:
            return json.loads(r["value"])
        return None

    def cache_put(self, key: str, value) -> None:
        with _lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO online_cache (key, value, fetched_at) VALUES (?,?,?)",
                              (key, json.dumps(value), int(time.time())))

    def cache_prune(self, max_age_sec: int) -> int:
        with _lock, self.conn:
            return self.conn.execute("DELETE FROM online_cache WHERE fetched_at < ?",
                                     (int(time.time()) - max_age_sec,)).rowcount

    def set_track_online(self, track_id: str, status: str, **f) -> None:
        cols = ("recco_id", "spotify_url", "bpm", "key", "energy", "danceability", "valence")
        with _lock, self.conn:
            self.conn.execute(
                f"INSERT OR REPLACE INTO track_online (track_id, status, {', '.join(cols)}, checked_at) "
                f"VALUES (?,?,{','.join('?' * len(cols))},?)",
                (track_id, status, *(f.get(c) for c in cols), int(time.time())))

    def track_online(self) -> dict[str, dict]:
        return {r["track_id"]: dict(r) for r in self.conn.execute("SELECT * FROM track_online")}

    def put_online_tracks(self, tracks: list[dict]) -> None:
        with _lock, self.conn:
            for t in tracks:
                self.conn.execute("INSERT OR REPLACE INTO online_tracks (id, data, seen_at) VALUES (?,?,?)",
                                  (t["id"], json.dumps(t), int(time.time())))

    def online_tracks(self) -> dict[str, dict]:
        return {r["id"]: json.loads(r["data"]) for r in self.conn.execute("SELECT id, data FROM online_tracks")}

    def recordings_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Recordings whose time span overlaps [start, end]."""
        rows = self.conn.execute(
            """SELECT * FROM recordings WHERE started_at IS NOT NULL AND error IS NULL
               AND started_at <= ? AND datetime(started_at, '+' || CAST(duration_sec AS INTEGER) || ' seconds') >= ?""",
            (end_iso.replace("T", " "), start_iso.replace("T", " "))).fetchall()
        return [dict(r) for r in rows]
