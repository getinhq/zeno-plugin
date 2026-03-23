from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class CacheEntry:
    content_id: str
    filename: str
    rel_path: str
    size_bytes: int
    last_accessed_at: int
    created_at: int


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
              content_id TEXT PRIMARY KEY,
              filename TEXT NOT NULL,
              rel_path TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              last_accessed_at INTEGER NOT NULL,
              created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_last_accessed ON entries(last_accessed_at);")


def upsert_entry(db_path: Path, *, content_id: str, filename: str, rel_path: str, size_bytes: int) -> None:
    now = int(time.time())
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entries(content_id, filename, rel_path, size_bytes, last_accessed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(content_id) DO UPDATE SET
              filename=excluded.filename,
              rel_path=excluded.rel_path,
              size_bytes=excluded.size_bytes,
              last_accessed_at=excluded.last_accessed_at
            """,
            (content_id, filename, rel_path, int(size_bytes), now, now),
        )


def touch(db_path: Path, *, content_id: str) -> None:
    now = int(time.time())
    with _connect(db_path) as conn:
        conn.execute("UPDATE entries SET last_accessed_at = ? WHERE content_id = ?", (now, content_id))


def delete_entry(db_path: Path, *, content_id: str) -> None:
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM entries WHERE content_id = ?", (content_id,))


def total_size_bytes(db_path: Path) -> int:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM entries").fetchone()
        return int(row[0] if row else 0)


def get_entry(db_path: Path, *, content_id: str) -> Optional[CacheEntry]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT content_id, filename, rel_path, size_bytes, last_accessed_at, created_at FROM entries WHERE content_id = ?",
            (content_id,),
        ).fetchone()
        if not row:
            return None
        return CacheEntry(
            content_id=row[0],
            filename=row[1],
            rel_path=row[2],
            size_bytes=int(row[3]),
            last_accessed_at=int(row[4]),
            created_at=int(row[5]),
        )


def eviction_candidates(db_path: Path, *, exclude_content_ids: Iterable[str] = (), limit: int = 100) -> list[CacheEntry]:
    exclude = list(dict.fromkeys(exclude_content_ids))
    q = "SELECT content_id, filename, rel_path, size_bytes, last_accessed_at, created_at FROM entries"
    args: list[object] = []
    if exclude:
        placeholders = ",".join(["?"] * len(exclude))
        q += f" WHERE content_id NOT IN ({placeholders})"
        args.extend(exclude)
    q += " ORDER BY last_accessed_at ASC LIMIT ?"
    args.append(int(limit))

    with _connect(db_path) as conn:
        rows = conn.execute(q, args).fetchall()
    return [
        CacheEntry(
            content_id=r[0],
            filename=r[1],
            rel_path=r[2],
            size_bytes=int(r[3]),
            last_accessed_at=int(r[4]),
            created_at=int(r[5]),
        )
        for r in rows
    ]

