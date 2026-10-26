import os
import sqlite3
from typing import Any


class MemoryStore:
    def __init__(self, db_path: str = "~/.local/share/babyai/memory.db"):
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL;")
        self.connection.execute("PRAGMA foreign_keys=ON;")
        self._initialize_schema()

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
        self.connection.close()

    def remember_fact(self, fact: str, source: str | None = None) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO facts (fact, source) VALUES (?, ?)",
            (fact, source),
        )
        self.connection.commit()
        return {"id": cursor.lastrowid, "fact": fact, "source": source}

    def recall_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        sql = """
            SELECT f.id, f.fact, f.source, f.created_at
            FROM facts f
            JOIN facts_fts ft ON ft.rowid = f.id
            WHERE facts_fts MATCH ?
            ORDER BY bm25(facts_fts) LIMIT ?
        """
        rows = self.connection.execute(sql, (query, limit)).fetchall()
        return [dict(row) for row in rows]

    def forget_memory(self, fact_id: int | str | None = None, query: str | None = None) -> int:
        if fact_id is not None:
            cursor = self.connection.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self.connection.commit()
            return cursor.rowcount
        if query is not None:
            cursor = self.connection.execute("DELETE FROM facts WHERE facts MATCH ?", (query,))
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
        sql = """
            SELECT c.id, c.session_id, c.role, c.content, c.created_at
            FROM conversations c
            JOIN conversations_fts ft ON ft.rowid = c.id
            WHERE conversations_fts MATCH ?
            ORDER BY bm25(conversations_fts) LIMIT ?
        """
        rows = self.connection.execute(sql, (query, limit)).fetchall()
        return [dict(row) for row in rows]

    def search_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        facts = self.recall_memory(query, limit=limit)
        conversations = self.search_conversations(query, limit=limit)
        return facts + conversations
