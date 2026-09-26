"""SQLite persistence.

Tables:
  conversations  - chat threads (one per day by default)
  messages       - the raw Claude API history of each conversation, append-only, replayed verbatim
  cards          - the notebook: every sentence the user asked about, with its review state
  review_log     - one row per flashcard answer
  learner_notes  - long-term memory of the learner's recurring weak points
  settings       - small key/value preferences (never the API key; that lives in the Keychain)
"""

from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import srs

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    profile     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY,
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content_json     TEXT NOT NULL,
    input_tokens     INTEGER,
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS cards (
    id                INTEGER PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind IN ('zh2en', 'en2zh', 'check')),
    zh                TEXT NOT NULL,
    en                TEXT NOT NULL,
    source            TEXT NOT NULL DEFAULT '',
    detail_json       TEXT NOT NULL DEFAULT '{}',
    conversation_id   INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    tool_use_id       TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL,
    level             INTEGER NOT NULL DEFAULT 0,
    due_at            REAL,
    last_reviewed_at  REAL,
    reviews           INTEGER NOT NULL DEFAULT 0,
    lapses            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS cards_by_due ON cards(due_at);
CREATE INDEX IF NOT EXISTS cards_by_created ON cards(created_at);
CREATE INDEX IF NOT EXISTS cards_by_tool_use ON cards(tool_use_id);

CREATE TABLE IF NOT EXISTS review_log (
    id           INTEGER PRIMARY KEY,
    card_id      INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    remembered   INTEGER NOT NULL,
    level_after  INTEGER NOT NULL,
    reviewed_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS learner_notes (
    id          INTEGER PRIMARY KEY,
    topic       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    note        TEXT NOT NULL,
    example     TEXT NOT NULL DEFAULT '',
    count       INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    id               INTEGER PRIMARY KEY,
    conversation_id  INTEGER,
    model            TEXT NOT NULL,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    cache_read       INTEGER NOT NULL DEFAULT 0,
    cache_write      INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL
);
"""

CARD_KINDS = ("zh2en", "en2zh", "check")
CARD_FILTERS = ("all", "due", "weak", "known")
REVIEW_SCOPES = ("due", "today", "weak", "all")


def _now() -> float:
    return time.time()


def _card_row(row: sqlite3.Row, now: float | None = None) -> dict[str, Any]:
    card = dict(row)
    card["detail"] = json.loads(card.pop("detail_json") or "{}")
    card["due"] = srs.is_due(card["due_at"], now)
    return card


class Store:
    """Thread-safe wrapper around one SQLite connection."""

    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA foreign_keys = ON")
            if str(path) != ":memory:":
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _all(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchall()

    def _one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchone()

    def _run(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, tuple(args))

    # ---------- settings ----------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._run(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---------- conversations ----------

    def create_conversation(self, profile: str = "", title: str = "") -> int:
        now = _now()
        cur = self._run(
            "INSERT INTO conversations(title, profile, created_at, updated_at) VALUES(?, ?, ?, ?)",
            (title, profile, now, now),
        )
        return int(cur.lastrowid)

    def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return dict(row) if row else None

    def list_conversations(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self._all(
            """
            SELECT c.*,
                   (SELECT COUNT(*) FROM cards k WHERE k.conversation_id = c.id) AS card_count,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    def latest_conversation(self) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 1")
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: int) -> None:
        self._run("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def set_conversation_title(self, conversation_id: int, title: str) -> None:
        self._run("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))

    # ---------- message history ----------

    def append_message(
        self, conversation_id: int, role: str, content: list[dict[str, Any]], input_tokens: int | None = None
    ) -> int:
        now = _now()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO messages(conversation_id, role, content_json, input_tokens, created_at) VALUES(?, ?, ?, ?, ?)",
                (conversation_id, role, json.dumps(content, ensure_ascii=False), input_tokens, now),
            )
            self._db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            return int(cur.lastrowid)

    def load_history(self, conversation_id: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT role, content_json FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
        )
        return [{"role": r["role"], "content": json.loads(r["content_json"])} for r in rows]

    def load_history_rows(self, conversation_id: int) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT id, role, content_json, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        )
        return [
            {"id": r["id"], "role": r["role"], "content": json.loads(r["content_json"]), "created_at": r["created_at"]}
            for r in rows
        ]

    def last_input_tokens(self, conversation_id: int) -> int:
        row = self._one(
            "SELECT input_tokens FROM messages WHERE conversation_id = ? AND input_tokens IS NOT NULL ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        )
        return int(row["input_tokens"]) if row else 0

    # ---------- cards (the notebook) ----------

    def add_card(
        self,
        kind: str,
        zh: str,
        en: str,
        *,
        source: str = "",
        detail: dict[str, Any] | None = None,
        conversation_id: int | None = None,
        tool_use_id: str | None = None,
    ) -> int:
        if kind not in CARD_KINDS:
            raise ValueError(f"unknown card kind: {kind}")
        now = _now()
        cur = self._run(
            """
            INSERT INTO cards(kind, zh, en, source, detail_json, conversation_id, tool_use_id, created_at, updated_at, due_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                zh.strip(),
                en.strip(),
                source.strip(),
                json.dumps(detail or {}, ensure_ascii=False),
                conversation_id,
                tool_use_id,
                now,
                now,
                now,
            ),
        )
        return int(cur.lastrowid)

    def get_card(self, card_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM cards WHERE id = ?", (card_id,))
        return _card_row(row) if row else None

    def update_card(self, card_id: int, *, zh: str | None = None, en: str | None = None) -> dict[str, Any] | None:
        fields, args = [], []
        if zh is not None:
            fields.append("zh = ?")
            args.append(zh.strip())
        if en is not None:
            fields.append("en = ?")
            args.append(en.strip())
        if fields:
            fields.append("updated_at = ?")
            args.append(_now())
            self._run(f"UPDATE cards SET {', '.join(fields)} WHERE id = ?", (*args, card_id))
        return self.get_card(card_id)

    def set_card_tool_use(self, card_id: int, tool_use_id: str) -> None:
        self._run("UPDATE cards SET tool_use_id = ? WHERE id = ?", (tool_use_id, card_id))

    def cards_by_tool_use(self, tool_use_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(tool_use_ids)
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self._all(f"SELECT * FROM cards WHERE tool_use_id IN ({marks})", ids)
        return {r["tool_use_id"]: _card_row(r) for r in rows}

    def delete_card(self, card_id: int) -> bool:
        return self._run("DELETE FROM cards WHERE id = ?", (card_id,)).rowcount > 0

    def list_cards(
        self, *, filter: str = "all", query: str = "", limit: int = 500, offset: int = 0
    ) -> list[dict[str, Any]]:
        where, args = self._card_filter(filter, query)
        rows = self._all(
            f"SELECT * FROM cards {where} ORDER BY created_at DESC LIMIT ? OFFSET ?", (*args, limit, offset)
        )
        now = _now()
        return [_card_row(r, now) for r in rows]

    def count_cards(self, *, filter: str = "all", query: str = "") -> int:
        where, args = self._card_filter(filter, query)
        return int(self._one(f"SELECT COUNT(*) AS n FROM cards {where}", args)["n"])

    def _card_filter(self, filter: str, query: str) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if filter == "due":
            clauses.append("(due_at IS NULL OR due_at <= ?)")
            args.append(_now())
        elif filter == "weak":
            clauses.append("reviews > 0 AND level = 0")
        elif filter == "known":
            clauses.append("level >= ?")
            args.append(srs.KNOWN_LEVEL)
        q = query.strip()
        if q:
            clauses.append("(zh LIKE ? OR en LIKE ? OR source LIKE ?)")
            like = f"%{q}%"
            args += [like, like, like]
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", args

    def search_cards(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Notebook search for the agent: every whitespace-separated term must match somewhere."""
        terms = [t for t in query.split() if t][:6]
        if not terms:
            return self.list_cards(limit=limit)
        clauses = " AND ".join("(zh LIKE ? OR en LIKE ? OR source LIKE ?)" for _ in terms)
        args: list[Any] = []
        for t in terms:
            args += [f"%{t}%"] * 3
        rows = self._all(f"SELECT * FROM cards WHERE {clauses} ORDER BY created_at DESC LIMIT ?", (*args, limit))
        return [_card_row(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        now = _now()
        today = srs.start_of_day(now)
        row = self._one(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN due_at IS NULL OR due_at <= ? THEN 1 ELSE 0 END) AS due,
                   SUM(CASE WHEN level >= ? THEN 1 ELSE 0 END) AS known,
                   SUM(CASE WHEN reviews > 0 AND level = 0 THEN 1 ELSE 0 END) AS weak,
                   SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS today
            FROM cards
            """,
            (now, srs.KNOWN_LEVEL, today),
        )
        reviewed_today = self._one("SELECT COUNT(*) AS n FROM review_log WHERE reviewed_at >= ?", (today,))["n"]
        return {
            "total": row["total"] or 0,
            "due": row["due"] or 0,
            "known": row["known"] or 0,
            "weak": row["weak"] or 0,
            "today": row["today"] or 0,
            "reviewed_today": reviewed_today or 0,
        }

    # ---------- review ----------

    def review_queue(self, scope: str = "due", limit: int = 20) -> list[dict[str, Any]]:
        now = _now()
        if scope == "due":
            rows = self._all(
                "SELECT * FROM cards WHERE due_at IS NULL OR due_at <= ? ORDER BY due_at, created_at LIMIT ?",
                (now, limit),
            )
        elif scope == "today":
            rows = self._all(
                "SELECT * FROM cards WHERE created_at >= ? ORDER BY created_at LIMIT ?",
                (srs.start_of_day(now), limit),
            )
        elif scope == "weak":
            rows = self._all(
                "SELECT * FROM cards WHERE reviews > 0 AND level = 0 ORDER BY RANDOM() LIMIT ?", (limit,)
            )
        elif scope == "all":
            rows = self._all("SELECT * FROM cards ORDER BY RANDOM() LIMIT ?", (limit,))
        else:
            raise ValueError(f"unknown review scope: {scope}")
        cards = [_card_row(r, now) for r in rows]
        if scope in ("today",):
            random.shuffle(cards)
        return cards

    def scope_counts(self) -> dict[str, int]:
        now = _now()
        return {
            "due": self.count_cards(filter="due"),
            "today": int(
                self._one("SELECT COUNT(*) AS n FROM cards WHERE created_at >= ?", (srs.start_of_day(now),))["n"]
            ),
            "weak": self.count_cards(filter="weak"),
            "all": self.count_cards(),
        }

    def record_review(self, card_id: int, remembered: bool) -> dict[str, Any] | None:
        card = self.get_card(card_id)
        if card is None:
            return None
        now = _now()
        level, due_at = srs.schedule(card["level"], remembered, now)
        with self._lock:
            self._db.execute(
                """
                UPDATE cards SET level = ?, due_at = ?, last_reviewed_at = ?, reviews = reviews + 1,
                                 lapses = lapses + ?
                WHERE id = ?
                """,
                (level, due_at, now, 0 if remembered else 1, card_id),
            )
            self._db.execute(
                "INSERT INTO review_log(card_id, remembered, level_after, reviewed_at) VALUES(?, ?, ?, ?)",
                (card_id, int(remembered), level, now),
            )
        return self.get_card(card_id)

    # ---------- learner notes (long-term memory) ----------

    def upsert_note(self, topic: str, note: str, example: str = "") -> dict[str, Any]:
        now = _now()
        topic = topic.strip()
        self._run(
            """
            INSERT INTO learner_notes(topic, note, example, created_at, updated_at) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(topic) DO UPDATE SET
                note = excluded.note,
                example = CASE WHEN excluded.example != '' THEN excluded.example ELSE learner_notes.example END,
                count = learner_notes.count + 1,
                updated_at = excluded.updated_at
            """,
            (topic, note.strip(), example.strip(), now, now),
        )
        return dict(self._one("SELECT * FROM learner_notes WHERE topic = ?", (topic,)))

    def top_notes(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM learner_notes ORDER BY count DESC, updated_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def delete_note(self, note_id: int) -> None:
        self._run("DELETE FROM learner_notes WHERE id = ?", (note_id,))

    # ---------- API usage (for the cost estimate in settings) ----------

    def record_usage(
        self,
        conversation_id: int | None,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> None:
        self._run(
            """
            INSERT INTO usage(conversation_id, model, input_tokens, output_tokens, cache_read, cache_write, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, model, input_tokens, output_tokens, cache_read, cache_write, _now()),
        )

    def usage_by_model(self, since: float) -> list[dict[str, Any]]:
        rows = self._all(
            """
            SELECT model, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                   SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write, COUNT(*) AS requests
            FROM usage WHERE created_at >= ? GROUP BY model
            """,
            (since,),
        )
        return [dict(r) for r in rows]
