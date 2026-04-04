"""Session management backed by SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_legacy_sessions_dir
from nanobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Messages remain append-oriented in memory so the rest of the agent stack
    can keep using the same structure as before.
    """

    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    @staticmethod
    def _find_legal_start(messages: list[dict[str, Any]]) -> int:
        """Find first index where every tool result has a matching assistant tool_call."""
        declared: set[str] = set()
        start = 0
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared.add(str(tc["id"]))
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid and str(tid) not in declared:
                    start = i + 1
                    declared.clear()
                    for prev in messages[start:i + 1]:
                        if prev.get("role") == "assistant":
                            for tc in prev.get("tool_calls") or []:
                                if isinstance(tc, dict) and tc.get("id"):
                                    declared.add(str(tc["id"]))
        return start

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a legal tool-call boundary."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        for i, message in enumerate(sliced):
            if message.get("role") == "user":
                sliced = sliced[i:]
                break

        start = self._find_legal_start(sliced)
        if start:
            sliced = sliced[start:]

        out: list[dict[str, Any]] = []
        for message in sliced:
            entry: dict[str, Any] = {"role": message["role"], "content": message.get("content", "")}
            for key in ("tool_calls", "tool_call_id", "name"):
                if key in message:
                    entry[key] = message[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()

    def retain_recent_legal_suffix(self, max_messages: int) -> None:
        """Keep a legal recent suffix, mirroring get_history boundary rules."""
        if max_messages <= 0:
            self.clear()
            return
        if len(self.messages) <= max_messages:
            return

        start_idx = max(0, len(self.messages) - max_messages)
        while start_idx > 0 and self.messages[start_idx].get("role") != "user":
            start_idx -= 1

        retained = self.messages[start_idx:]
        start = self._find_legal_start(retained)
        if start:
            retained = retained[start:]

        dropped = len(self.messages) - len(retained)
        self.messages = retained
        self.last_consolidated = max(0, self.last_consolidated - dropped)
        self.updated_at = datetime.now()


class SessionManager:
    """Manage conversation sessions with SQLite persistence."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        # 保留旧目录只是为了兼容迁移，新的持久化全部写入 SQLite。
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = get_legacy_sessions_dir()
        self.db_path = self.workspace / "session.db"
        self._cache: dict[str, Session] = {}
        self._init_db()
        self._migrate_legacy_jsonl()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        ensure_dir(self.db_path.parent)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_consolidated INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(session_key) REFERENCES sessions(key) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session_key
                    ON messages(session_key, idx);
                """
            )

    @staticmethod
    def _serialize_dt(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _parse_dt(value: str | None) -> datetime:
        if not value:
            return datetime.now()
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.now()

    @staticmethod
    def _preview_from_payload(raw_payload: str | None) -> str:
        if not raw_payload:
            return ""
        try:
            payload = json.loads(raw_payload)
        except Exception:
            return ""
        content = payload.get("content", "")
        if isinstance(content, str):
            preview = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            preview = " ".join(part for part in parts if part)
        else:
            preview = str(content)
        preview = " ".join(preview.split())
        return preview[:120]

    def _row_to_session(self, row: sqlite3.Row, messages: list[dict[str, Any]]) -> Session:
        metadata = {}
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            pass
        return Session(
            key=row["key"],
            messages=messages,
            created_at=self._parse_dt(row["created_at"]),
            updated_at=self._parse_dt(row["updated_at"]),
            metadata=metadata,
            last_consolidated=int(row["last_consolidated"] or 0),
        )

    def _load_messages(self, conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT payload_json FROM messages WHERE session_key = ? ORDER BY idx ASC, id ASC",
            (key,),
        ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            try:
                messages.append(json.loads(row["payload_json"]))
            except Exception:
                logger.warning("Failed to decode session message for {}", key)
        return messages

    def _load(self, key: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key, created_at, updated_at, metadata_json, last_consolidated FROM sessions WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row, self._load_messages(conn, key))

    def _get_jsonl_candidates(self, key: str) -> list[Path]:
        safe_key = safe_filename(key.replace(":", "_"))
        return [
            self.sessions_dir / f"{safe_key}.jsonl",
            self.legacy_sessions_dir / f"{safe_key}.jsonl",
        ]

    def _load_legacy_jsonl(self, path: Path, key: str) -> Session | None:
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    raw = line.strip()
                    if not raw:
                        continue
                    data = json.loads(raw)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = self._parse_dt(data.get("created_at"))
                        updated_at = self._parse_dt(data.get("updated_at"))
                        last_consolidated = int(data.get("last_consolidated", 0))
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated,
            )
        except Exception as exc:
            logger.warning("Failed to migrate legacy session {} from {}: {}", key, path, exc)
            return None

    def _migrate_one_jsonl(self, path: Path) -> None:
        key = ""
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline().strip()
            if first:
                data = json.loads(first)
                if data.get("_type") == "metadata" and data.get("key"):
                    key = str(data["key"])
        except Exception:
            key = ""

        if not key:
            key = path.stem.replace("_", ":", 1)

        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM sessions WHERE key = ?", (key,)).fetchone()
        if exists:
            return

        session = self._load_legacy_jsonl(path, key)
        if session is None:
            return
        self.save(session)
        logger.info("Migrated legacy session {} into SQLite", key)

    def _migrate_legacy_jsonl(self) -> None:
        # 这里保留原文件，只做导入，避免用户在迁移阶段丢历史数据。
        seen: set[Path] = set()
        for directory in (self.sessions_dir, self.legacy_sessions_dir):
            if not directory.exists():
                continue
            for path in directory.glob("*.jsonl"):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                self._migrate_one_jsonl(path)

    def get(self, key: str) -> Session | None:
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is not None:
            self._cache[key] = session
        return session

    def create_session(self, key: str, metadata: dict[str, Any] | None = None) -> Session:
        session = Session(key=key, metadata=metadata or {})
        self.save(session)
        return session

    def get_or_create(self, key: str) -> Session:
        session = self.get(key)
        if session is not None:
            return session
        session = Session(key=key)
        self._cache[key] = session
        return session

    def save(self, session: Session) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(key, created_at, updated_at, metadata_json, last_consolidated)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json,
                    last_consolidated = excluded.last_consolidated
                """,
                (
                    session.key,
                    self._serialize_dt(session.created_at),
                    self._serialize_dt(session.updated_at),
                    json.dumps(session.metadata, ensure_ascii=False),
                    int(session.last_consolidated),
                ),
            )
            conn.execute("DELETE FROM messages WHERE session_key = ?", (session.key,))
            conn.executemany(
                "INSERT INTO messages(session_key, idx, payload_json) VALUES (?, ?, ?)",
                [
                    (
                        session.key,
                        index,
                        json.dumps(message, ensure_ascii=False),
                    )
                    for index, message in enumerate(session.messages)
                ],
            )
        self._cache[session.key] = session

    def delete_session(self, key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE key = ?", (key,))
            deleted = cursor.rowcount > 0
        self.invalidate(key)
        return deleted

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    def list_sessions(self, prefix: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT
                s.key,
                s.created_at,
                s.updated_at,
                s.metadata_json,
                s.last_consolidated,
                COALESCE((SELECT COUNT(1) FROM messages m WHERE m.session_key = s.key), 0) AS message_count,
                (
                    SELECT payload_json
                    FROM messages m2
                    WHERE m2.session_key = s.key
                    ORDER BY m2.idx DESC, m2.id DESC
                    LIMIT 1
                ) AS latest_payload
            FROM sessions s
        """
        params: list[Any] = []
        if prefix:
            sql += " WHERE s.key LIKE ?"
            params.append(f"{prefix}%")
        sql += " ORDER BY s.updated_at DESC"

        sessions: list[dict[str, Any]] = []
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            sessions.append(
                {
                    "key": row["key"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "metadata": metadata,
                    "last_consolidated": int(row["last_consolidated"] or 0),
                    "message_count": int(row["message_count"] or 0),
                    "preview": self._preview_from_payload(row["latest_payload"]),
                }
            )
        return sessions

    def export_legacy_jsonl(self, key: str) -> Path | None:
        """Optional helper for manual inspection or troubleshooting."""
        session = self.get(key)
        if session is None:
            return None

        path = self.sessions_dir / f"{safe_filename(key.replace(':', '_'))}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": session.key,
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat(),
                        "metadata": session.metadata,
                        "last_consolidated": session.last_consolidated,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            for message in session.messages:
                fh.write(json.dumps(message, ensure_ascii=False) + "\n")
        return path
