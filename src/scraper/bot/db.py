import json
import sqlite3
from typing import Any

from bot.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes (
    message_id TEXT PRIMARY KEY,
    author_id TEXT,
    author_name TEXT,
    thread_id TEXT,
    posted_at TEXT,
    attachment_paths TEXT,
    message_content TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS critiques (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_message_id TEXT REFERENCES resumes(message_id),
    message_id TEXT,
    author_id TEXT,
    author_name TEXT,
    content TEXT,
    posted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_critiques_resume_message_id
    ON critiques(resume_message_id);

CREATE INDEX IF NOT EXISTS idx_resumes_thread_id
    ON resumes(thread_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_critiques_message_id
    ON critiques(message_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(resumes)").fetchall()
    }
    if "message_content" not in columns:
        conn.execute(
            "ALTER TABLE resumes ADD COLUMN message_content TEXT DEFAULT ''"
        )
        conn.commit()


def insert_resume(
    message_id: str,
    author_id: str,
    author_name: str,
    thread_id: str,
    posted_at: str,
    attachment_paths: list[str],
    message_content: str = "",
) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO resumes
                (message_id, author_id, author_name, thread_id, posted_at,
                 attachment_paths, message_content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                author_id,
                author_name,
                thread_id,
                posted_at,
                json.dumps(attachment_paths),
                message_content,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def update_resume_message_content(message_id: str, message_content: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE resumes
            SET message_content = ?
            WHERE message_id = ?
              AND (message_content IS NULL OR message_content = '')
            """,
            (message_content, message_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def insert_critique(
    resume_message_id: str,
    message_id: str,
    author_id: str,
    author_name: str,
    content: str,
    posted_at: str,
) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO critiques
                (resume_message_id, message_id, author_id, author_name, content, posted_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                resume_message_id,
                message_id,
                author_id,
                author_name,
                content,
                posted_at,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def resume_exists(message_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM resumes WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return row is not None


def get_resume(message_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM resumes WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return dict(row) if row else None


def get_resume_message_ids() -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT message_id FROM resumes").fetchall()
        return {row["message_id"] for row in rows}


def update_resume_thread_id(message_id: str, thread_id: str) -> bool:
    if not thread_id or thread_id == message_id:
        return False

    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE resumes
            SET thread_id = ?
            WHERE message_id = ?
              AND (thread_id IS NULL OR thread_id = '')
            """,
            (thread_id, message_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def critique_exists(message_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM critiques WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        return row is not None


def get_thread_ids() -> set[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT thread_id FROM resumes WHERE thread_id IS NOT NULL"
        ).fetchall()
        return {row["thread_id"] for row in rows}


def get_resume_by_thread_id(thread_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM resumes WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def get_all_resumes() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM resumes ORDER BY posted_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_critiques_for_resume(resume_message_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM critiques
            WHERE resume_message_id = ?
            ORDER BY posted_at ASC
            """,
            (resume_message_id,),
        ).fetchall()
        return [dict(row) for row in rows]
