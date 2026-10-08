#!/usr/bin/env python3
"""
db.py — SQLite Database Helper
Single source of truth for all clip state. Used by:
  - pipeline.py  (writes clips after processing)
  - dashboard/app.py  (reads clips, writes approval/rejection/post results)
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "db.sqlite"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe for concurrent reads/writes
    return conn


def init_db(db_path: Path = DB_PATH):
    """Create tables if they don't exist yet."""
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vods (
                id           TEXT PRIMARY KEY,
                title        TEXT,
                channel      TEXT,
                local_path   TEXT,
                status       TEXT DEFAULT 'processing',
                created_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS clips (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                vod_id          TEXT NOT NULL,
                clip_index      INTEGER,
                clip_path       TEXT,
                captioned_path  TEXT,
                start_time      REAL,
                end_time        REAL,
                duration        REAL,
                peak_time       REAL,
                audio_score     REAL,
                ai_score        INTEGER,
                ai_description  TEXT,
                combined_score  REAL,
                transcript      TEXT,
                -- Platform metadata (JSON strings)
                metadata_json   TEXT,
                -- Review state
                status          TEXT DEFAULT 'pending',
                created_at      TEXT,
                reviewed_at     TEXT,
                -- Post results
                post_results    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
            CREATE INDEX IF NOT EXISTS idx_clips_vod    ON clips(vod_id);
        """)


# ─── VOD helpers ──────────────────────────────────────────────────────────────

def upsert_vod(vod_id: str, title: str, channel: str, local_path: str,
               status: str = "processing", db_path: Path = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO vods (id, title, channel, local_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                local_path = excluded.local_path,
                status     = excluded.status
        """, (vod_id, title, channel, local_path, status,
              datetime.now(timezone.utc).isoformat()))


def update_vod_status(vod_id: str, status: str, db_path: Path = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute("UPDATE vods SET status=? WHERE id=?", (status, vod_id))


# ─── Clip helpers ─────────────────────────────────────────────────────────────

def insert_clip(clip: dict, vod_id: str, db_path: Path = DB_PATH) -> int:
    """Insert a processed clip and return its row id."""
    meta = clip.get("metadata", {})
    with get_connection(db_path) as conn:
        cur = conn.execute("""
            INSERT INTO clips (
                vod_id, clip_index, clip_path, captioned_path,
                landscape_path, portrait_crop_path, portrait_blackbg_path,
                start_time, end_time, duration, peak_time,
                audio_score, ai_score, ai_description, combined_score,
                transcript, words_json, metadata_json, status, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            vod_id,
            clip.get("clip_index"),
            clip.get("clip_path"),
            clip.get("captioned_path"),
            clip.get("landscape_path"),
            clip.get("portrait_crop_path"),
            clip.get("portrait_blackbg_path"),
            clip.get("start"),
            clip.get("end"),
            clip.get("duration"),
            clip.get("peak_time"),
            clip.get("score"),
            clip.get("ai_score"),
            clip.get("ai_description"),
            clip.get("combined_score"),
            clip.get("transcript"),
            clip.get("words_json"),
            json.dumps(meta),
            "pending",
            datetime.now(timezone.utc).isoformat(),
        ))
        return cur.lastrowid


def get_pending_clips(db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT clips.*, vods.channel as channel
               FROM clips LEFT JOIN vods ON clips.vod_id = vods.id
               WHERE clips.status='pending' ORDER BY clips.created_at DESC"""
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_clip(clip_id: int, db_path: Path = DB_PATH) -> dict:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """SELECT clips.*, vods.channel as channel
               FROM clips LEFT JOIN vods ON clips.vod_id = vods.id
               WHERE clips.id=?""",
            (clip_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def approve_clip(clip_id: int, db_path: Path = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='approved', reviewed_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), clip_id),
        )


def reject_clip(clip_id: int, db_path: Path = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='rejected', reviewed_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), clip_id),
        )


def update_captioned_path(clip_id: int, path: str, db_path: Path = DB_PATH):
    """Update the captioned_path for a clip (e.g. after burning a caption overlay)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET captioned_path=? WHERE id=?",
            (path, clip_id),
        )


def update_clip_video_paths(
    clip_id: int,
    landscape_path: str = None,
    portrait_crop_path: str = None,
    portrait_blackbg_path: str = None,
    words_json: str = None,
    db_path: Path = DB_PATH,
):
    """Update all three captioned video paths + words after a reburn."""
    fields, vals = [], []
    if landscape_path       is not None: fields.append("landscape_path=?");        vals.append(landscape_path)
    if portrait_crop_path   is not None: fields.append("portrait_crop_path=?");    vals.append(portrait_crop_path)
    if portrait_blackbg_path is not None: fields.append("portrait_blackbg_path=?"); vals.append(portrait_blackbg_path)
    if words_json            is not None: fields.append("words_json=?");            vals.append(words_json)
    # keep captioned_path in sync with landscape for the Telegram bot
    if landscape_path       is not None: fields.append("captioned_path=?");        vals.append(landscape_path)
    if not fields:
        return
    vals.append(clip_id)
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE clips SET {', '.join(fields)} WHERE id=?", vals)


def update_clip_metadata(clip_id: int, metadata: dict, db_path: Path = DB_PATH):
    """Overwrite the metadata_json for a clip (user edits from dashboard)."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET metadata_json=? WHERE id=?",
            (json.dumps(metadata), clip_id),
        )


def mark_posted(clip_id: int, post_results: dict, db_path: Path = DB_PATH):
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE clips SET status='posted', post_results=?, reviewed_at=? WHERE id=?",
            (json.dumps(post_results), datetime.now(timezone.utc).isoformat(), clip_id),
        )


def get_recent_clips(limit: int = 50, db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM clips ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ─── Internal ─────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("metadata_json", "post_results"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ─── Init on import ───────────────────────────────────────────────────────────
init_db()
