"""Durable storage for plans, task states and worker artifacts.

The same SQLite choices as the main memory store are used here: one connection
per thread, ``DELETE`` journaling (no stray ``-wal`` file on a USB stick) and
``FULL`` sync (a finished task is on disk before the next one starts).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from babyai.orchestrator.models import Task


class TaskStore:
    def __init__(self, directory: str = "~/.local/share/babyai/orchestrator", filename: str = "tasks.db"):
        self.db_path = os.path.expanduser(os.path.join(directory, filename))
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._local = threading.local()
        self.last_write_error: str | None = None
        self._initialize_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.db_path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            self._local.connection = connection
        return connection

    def _initialize_schema(self) -> None:
        try:
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS plans (
                       id TEXT PRIMARY KEY,
                       goal TEXT NOT NULL,
                       status TEXT NOT NULL,
                       created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                       updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                       plan_id TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       data TEXT NOT NULL,
                       PRIMARY KEY (plan_id, task_id)
                   )"""
            )
            self.connection.execute(
                """CREATE TABLE IF NOT EXISTS artifacts (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       plan_id TEXT NOT NULL,
                       task_id TEXT NOT NULL,
                       content TEXT NOT NULL,
                       source TEXT,
                       created_at TEXT DEFAULT CURRENT_TIMESTAMP
                   )"""
            )
            self.connection.commit()
        except (sqlite3.DatabaseError, OSError) as exc:
            self.last_write_error = f"initialize: {exc}"
            raise

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def _failed(self, exc: BaseException, what: str) -> bool:
        text = str(exc).lower()
        gone = any(marker in text for marker in (
            "disk i/o error", "input/output error", "no such device", "device not configured",
            "unable to open database file", "database or disk is full", "read-only database",
        ))
        if gone:
            self.last_write_error = f"{what}: {exc}"
        return gone

    def create_plan(self, plan_id: str, goal: str, tasks: list[Task]) -> bool:
        try:
            connection = self.connection
            connection.execute(
                "INSERT OR REPLACE INTO plans (id, goal, status, updated_at) VALUES (?, ?, 'running', CURRENT_TIMESTAMP)",
                (plan_id, goal),
            )
            for task in tasks:
                connection.execute(
                    "INSERT OR REPLACE INTO tasks (plan_id, task_id, data) VALUES (?, ?, ?)",
                    (plan_id, task.id, json.dumps(task.to_dict())),
                )
            connection.commit()
            return True
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "create_plan"):
                return False
            raise

    def set_plan_status(self, plan_id: str, status: str) -> bool:
        try:
            self.connection.execute(
                "UPDATE plans SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, plan_id),
            )
            self.connection.commit()
            return True
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "set_plan_status"):
                return False
            raise

    def update_task(self, plan_id: str, task: Task) -> bool:
        try:
            self.connection.execute(
                "UPDATE tasks SET data = ? WHERE plan_id = ? AND task_id = ?",
                (json.dumps(task.to_dict()), plan_id, task.id),
            )
            self.connection.commit()
            return True
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "update_task"):
                return False
            raise

    def record_artifact(self, plan_id: str, task_id: str, content: str, source: str = "") -> bool:
        try:
            self.connection.execute(
                "INSERT INTO artifacts (plan_id, task_id, content, source) VALUES (?, ?, ?, ?)",
                (plan_id, task_id, content, source),
            )
            self.connection.commit()
            return True
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "record_artifact"):
                return False
            raise

    def artifacts_for(self, plan_id: str, task_id: str | None = None) -> list[str]:
        try:
            if task_id is None:
                rows = self.connection.execute(
                    "SELECT content FROM artifacts WHERE plan_id = ? ORDER BY id", (plan_id,)
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT content FROM artifacts WHERE plan_id = ? AND task_id = ? ORDER BY id",
                    (plan_id, task_id),
                ).fetchall()
            return [str(row["content"]) for row in rows]
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "artifacts_for"):
                return []
            raise

    def get_tasks(self, plan_id: str) -> list[Task]:
        try:
            rows = self.connection.execute(
                "SELECT data FROM tasks WHERE plan_id = ? ORDER BY rowid", (plan_id,)
            ).fetchall()
            return [Task.from_dict(json.loads(str(row["data"]))) for row in rows]
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "get_tasks"):
                return []
            raise

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        try:
            row = self.connection.execute(
                "SELECT id, goal, status, created_at, updated_at FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "get_plan"):
                return None
            raise

    def recent_plans(self, limit: int = 10) -> list[dict[str, Any]]:
        try:
            rows = self.connection.execute(
                "SELECT id, goal, status, created_at, updated_at FROM plans ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        except (sqlite3.DatabaseError, OSError) as exc:
            if self._failed(exc, "recent_plans"):
                return []
            raise
