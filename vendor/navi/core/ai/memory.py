import os
import sqlite3
from pathlib import Path
from datetime import datetime


def _get_app_data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "AIPetDesktop" / "data"
    return Path(__file__).parent.parent.parent / "data"

DB_PATH = _get_app_data_dir() / "memory.db"

class MemoryManager:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT NOT NULL,
                summary   TEXT NOT NULL,
                msg_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.conn.commit()

    # session count

    def get_session_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return row[0] if row else 0

    def is_first_time(self) -> bool:
        return self.get_session_count() == 0

    # summary

    def save_summary(self, summary: str, msg_count: int = 0):
        self.conn.execute(
            "INSERT INTO sessions (date, summary, msg_count) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), summary, msg_count)
        )
        self.conn.commit()

    def get_recent_summaries(self, n: int = 3) -> list[str]:
        rows = self.conn.execute(
            "SELECT summary FROM sessions ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [r[0] for r in reversed(rows)]

    def build_memory_context(self) -> str:
        summaries = self.get_recent_summaries(3)
        if not summaries:
            return ""
        parts = "\n".join(f"- {s}" for s in summaries)
        return f"What you remember from previous conversations:\n{parts}\n"

    def close(self):
        self.conn.close()