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

# Meaning-based recall ("what do I like to eat?" should find "favourite food is ramen"). With no model and no
# extra dependency, that means a small hand-written map of everyday concepts plus light word matching. It is
# deliberately shallow: enough to bridge eat/food/meal or cat/pet, not a claim to understand language.
CONCEPTS: dict[str, frozenset[str]] = {
    "food": frozenset(
        "eat eats eating ate eaten food foods meal meals dish dishes cook cooks cooking cooked restaurant restaurants "
        "snack snacks hungry cuisine recipe recipes breakfast lunch dinner pizza ramen pasta noodles".split()),
    "drink": frozenset("drink drinks drinking drank coffee tea water beer wine juice".split()),
    "pet": frozenset("pet pets cat cats dog dogs kitten kittens puppy puppies animal animals".split()),
    "music": frozenset(
        "music song songs band bands guitar guitars piano album albums concert concerts listen listening".split()),
    "work": frozenset("work works working job jobs office boss career colleague colleagues meeting meetings".split()),
    "home": frozenset("home house flat apartment city town live lives living moved move neighbourhood neighborhood".split()),
    "sleep": frozenset("sleep sleeps sleeping slept bed bedtime tired nap early late morning night".split()),
    "study": frozenset("study studies studying learn learning course courses class classes school university".split()),
    "game": frozenset("game games gaming gamer play plays playing player players steam".split()),
    "code": frozenset("code coding program programming developer software python javascript rust".split()),
    "travel": frozenset("travel travels travelling trip trips holiday vacation flight fly flying".split()),
}
WORD_CONCEPT = {word: concept for concept, words in CONCEPTS.items() for word in words}

# Extra words that carry no subject for meaning-based recall (the FTS query keeps its own list so exact search is unchanged).
SEMANTIC_STOPWORDS = STOPWORDS | frozenset(
    "like likes liked have has had any some want wants need needs would should get gets got make makes made really just "
    "also thing things stuff favourite favorite".split()
)
SEMANTIC_MIN_SCORE = 0.30  # below this a shared concept is coincidence, not recall


def semantic_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", str(text).lower())
    return [word for word in words if len(word) > 1 and word not in SEMANTIC_STOPWORDS]


def _shares_stem(word: str, other: str) -> bool:
    """True when one word is the other with something added to it ("eat"/"eating", "cats"/"cat").

    A short stem only counts when little was added, so "cat" does not match "category" or "pro" "program"."""
    short, long = sorted((word, other), key=len)
    if not long.startswith(short):
        return False
    return len(short) >= 5 or (len(short) >= 3 and len(long) - len(short) <= 3)


def semantic_score(query_tokens: list[str], fact_tokens: list[str]) -> float:
    """How much a stored fact is about the same thing as the query, from 0 to about 1.

    A word that appears in both counts most; a shared beginning ("eat"/"eating") counts half; sharing one of the
    small concepts above counts three quarters. The sum is divided by the number of different query words, so a
    long question is not automatically a better match than a short one."""
    query = set(query_tokens)
    fact = set(fact_tokens)
    if not query or not fact:
        return 0.0
    matched = 0.0
    for word in query:
        if word in fact:
            matched += 1.0
        elif len(word) >= 4 and any(_shares_stem(word, other) for other in fact):
            matched += 0.5
        else:
            concept = WORD_CONCEPT.get(word)
            if concept is not None and any(WORD_CONCEPT.get(other) == concept for other in fact):
                matched += 0.75
    return matched / (len(query) * 1.5)



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

    def recent_facts(self, limit: int = 20) -> list[dict[str, Any]]:
        """The newest facts, for "what do you know about me?"."""
        limit = max(1, min(int(limit or 1), 100))
        rows = self.connection.execute(
            "SELECT id, fact, source, created_at FROM facts ORDER BY id DESC LIMIT ?",
            (limit,),
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

    def search_semantic(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Facts that are about the same thing as the query even when they share no exact word with it.

        Reads only facts (conversations are still searched by exact words), scores them in Python, and returns the
        best ones above ``SEMANTIC_MIN_SCORE``, newest first among equal scores."""
        query_tokens = semantic_tokens(query)
        if not query_tokens:
            return []
        rows = self.connection.execute(
            "SELECT id, fact, source, created_at FROM facts ORDER BY id DESC LIMIT 1000"
        ).fetchall()
        scored = []
        for row in rows:
            score = semantic_score(query_tokens, semantic_tokens(row["fact"]))
            if score >= SEMANTIC_MIN_SCORE:
                scored.append((score, row["id"], dict(row)))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in scored[: max(1, min(int(limit or 1), 50))]]

    def search_memory(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        facts = self.recall_memory(query, limit=limit)
        if len(facts) < limit:  # fill the empty places with meaning-based matches rather than nothing
            seen = {item["id"] for item in facts}
            for item in self.search_semantic(query, limit=limit - len(facts)):
                if item["id"] not in seen:
                    facts.append(item)
                    seen.add(item["id"])
        facts.extend(self.search_conversations(query, limit=limit))
        return facts
