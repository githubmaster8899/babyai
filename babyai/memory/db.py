import os
import re
import sqlite3
import threading
from typing import Any

# Words that say nothing about *what* is being looked up ("what do I like on pizza?" -> pizza, like).
STOPWORDS = frozenset(
    "a an the and or of to in on at for with is are was were be do does did what whats which who how my me i you your "
    "about that this it can could please tell know remember forget".split()
)


def fts_query(text: str, loose: bool) -> str | None:
    """Turn what a person (or a model) typed into a safe FTS5 query, or None if there is nothing to look for.

    Raw text breaks FTS5 on an apostrophe, question mark, hyphen or colon. Every word is quoted instead.
    ``loose`` matches facts that contain any of the words (for recall); otherwise all of them (for forgetting)."""
    words = re.findall(r"\w+", text.lower())
    words = [w for w in words if w not in STOPWORDS] or words
    if not words:
        return None
    return (" OR " if loose else " ").join(f'"{word}"' for word in words)


class MemoryStore:
    def __init__(self, db_path: str = "~/.local/share/babyai/memory.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self._initialize_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection. A sqlite3 connection can't be used from another thread, and the desktop
        overlay asks the brain from a worker thread, so each thread opens its own (every write commits)."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.db_path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._local.connection = connection
        return connection

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                source TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                fact,
                content='facts',
                content_rowid='id',
                tokenize='porter unicode61'
            )
            """
        )
        self.connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts
            BEGIN
                INSERT INTO facts_fts(rowid, fact) VALUES (new.id, new.fact);
            END
            """
        )
        self.connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts
            BEGIN
                INSERT INTO facts_fts(facts_fts, rowid, fact) VALUES ('delete', old.id, old.fact);
            END
            """
        )

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS conversations_fts USING fts5(
                content,
                content='conversations',
                content_rowid='id',
                tokenize='porter unicode61'
            )
            """
        )
        self.connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS conversations_ai AFTER INSERT ON conversations
            BEGIN
                INSERT INTO conversations_fts(rowid, content) VALUES (new.id, new.content);
            END
            """
        )
        self.connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS conversations_ad AFTER DELETE ON conversations
            BEGIN
                INSERT INTO conversations_fts(conversations_fts, rowid, content) VALUES ('delete', old.id, old.content);
            END
            """
        )
        self.connection.commit()

    def close(self) -> None:
        """Close this thread's connection (other threads' connections close when those threads end)."""
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def remember_fact(self, fact: str, source: str | None = None) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO facts (fact, source) VALUES (?, ?)",
            (fact, source),
        )
        self.connection.commit()
        return {"id": cursor.lastrowid, "fact": fact, "source": source}

    def recall_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        match = fts_query(query, loose=True)
        if match is None:
            return []
        rows = self.connection.execute(
            """
            SELECT f.id, f.fact, f.source, f.created_at
            FROM facts f
            WHERE f.id IN (
                SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?
            )
            ORDER BY f.id DESC
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def forget_memory(self, fact_id: int | None = None, query: str | None = None) -> int:
        if fact_id is not None:
            cursor = self.connection.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self.connection.commit()
            return cursor.rowcount
        if query is not None:
            match = fts_query(query, loose=False)  # every word must match: a vague request deletes nothing
            if match is None:
                return 0
            cursor = self.connection.execute(
                "DELETE FROM facts WHERE id IN (SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?)",
                (match,),
            )
            self.connection.commit()
            return cursor.rowcount
        return 0

    def append_conversation(self, session_id: str, role: str, content: str) -> None:
        self.connection.execute(
            "INSERT INTO conversations (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        self.connection.commit()

    def search_conversations(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        match = fts_query(query, loose=True)
        if match is None:
            return []
        rows = self.connection.execute(
            """
            SELECT c.id, c.session_id, c.role, c.content, c.created_at
            FROM conversations c
            WHERE c.id IN (
                SELECT rowid FROM conversations_fts WHERE conversations_fts MATCH ?
            )
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        results = self.recall_memory(query, limit=limit)
        results.extend(self.search_conversations(query, limit=limit))
        return results
