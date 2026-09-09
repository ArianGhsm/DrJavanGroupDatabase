from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sqlite3
import time

VALID_ACCESS_MODES = {"owner_only", "allowlist", "public"}
VALID_UPDATE_MODES = {"notify", "auto", "off"}
_UPDATE_LEASE_SECONDS = 180.0
_STATE_RETENTION_SECONDS = 7 * 24 * 3600

@dataclass(frozen=True, slots=True)
class BotUsageSummary:
    questions: int
    successes: int
    failures: int
    cache_hits: int
    ai_calls: int
    average_latency_ms: float

class BotStateStore:
    def __init__(self, path: Path, *, default_rate_limit: int = 6) -> None:
        self.path = path
        self.default_rate_limit = default_rate_limit
        self._ensure_schema()

    def access_mode(self) -> str:
        value = self.get_setting("access_mode") or "owner_only"
        return value if value in VALID_ACCESS_MODES else "owner_only"

    def set_access_mode(self, mode: str) -> None:
        if mode not in VALID_ACCESS_MODES: raise ValueError("invalid access mode")
        self.set_setting("access_mode", mode)

    def update_mode(self) -> str:
        value = self.get_setting("update_mode") or "notify"
        return value if value in VALID_UPDATE_MODES else "notify"

    def set_update_mode(self, mode: str) -> None:
        if mode not in VALID_UPDATE_MODES: raise ValueError("invalid update mode")
        self.set_setting("update_mode", mode)

    def rate_limit_per_minute(self) -> int:
        try: return max(1, min(60, int(self.get_setting("rate_limit_per_minute") or self.default_rate_limit)))
        except ValueError: return self.default_rate_limit

    def set_rate_limit_per_minute(self, value: int) -> None:
        if not 1 <= value <= 60: raise ValueError("rate limit out of range")
        self.set_setting("rate_limit_per_minute", str(value))

    def selected_model(self, default: str) -> str: return self.get_setting("model") or default
    def set_model(self, model: str) -> None: self.set_setting("model", model)

    def get_setting(self, key: str) -> str | None:
        with self._connect() as con:
            row = con.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
            return None if row is None else str(row[0])

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO bot_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def add_allowed_user(self, user_id: int) -> None:
        with self._connect() as con: con.execute("INSERT OR IGNORE INTO allowed_users(user_id) VALUES(?)", (int(user_id),))
    def remove_allowed_user(self, user_id: int) -> None:
        with self._connect() as con: con.execute("DELETE FROM allowed_users WHERE user_id=?", (int(user_id),))
    def allowed_users(self) -> tuple[int, ...]:
        with self._connect() as con: return tuple(int(r[0]) for r in con.execute("SELECT user_id FROM allowed_users ORDER BY user_id"))

    def is_allowed(self, user_id: int, owner_id: int) -> bool:
        if user_id == owner_id: return True
        mode = self.access_mode()
        if mode == "public": return True
        if mode == "owner_only": return False
        with self._connect() as con:
            return con.execute("SELECT 1 FROM allowed_users WHERE user_id=?", (int(user_id),)).fetchone() is not None

    def consume_rate_slot(self, user_id: int, *, owner_id: int, now: float | None = None) -> bool:
        if user_id == owner_id: return True
        now = time.time() if now is None else now; cutoff = now - 60.0; limit = self.rate_limit_per_minute(); con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE"); con.execute("DELETE FROM rate_events WHERE created_at<?", (cutoff,))
            count = int(con.execute("SELECT count(*) FROM rate_events WHERE user_id=? AND created_at>=?", (user_id, cutoff)).fetchone()[0])
            if count >= limit: con.rollback(); return False
            con.execute("INSERT INTO rate_events(user_id,created_at) VALUES(?,?)", (user_id, now)); con.commit(); return True
        finally: con.close()

    def mark_update_once(self, update_id: int) -> bool:
        with self._connect() as con:
            cur = con.execute("INSERT OR IGNORE INTO processed_updates(update_id,created_at) VALUES(?,?)", (int(update_id), time.time())); return cur.rowcount == 1

    def claim_update(self, update_id: int, *, now: float | None = None, lease_seconds: float = _UPDATE_LEASE_SECONDS) -> bool:
        now = time.time() if now is None else now; con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE"); row = con.execute("SELECT status,lease_until FROM update_claims WHERE update_id=?", (int(update_id),)).fetchone()
            if row is None:
                con.execute("INSERT INTO update_claims(update_id,status,lease_until,updated_at) VALUES(?,?,?,?)", (int(update_id), "processing", now + lease_seconds, now)); con.commit(); return True
            status, lease_until = str(row[0]), float(row[1] or 0)
            if status == "done" or lease_until > now: con.rollback(); return False
            con.execute("UPDATE update_claims SET status='processing',lease_until=?,updated_at=? WHERE update_id=?", (now + lease_seconds, now, int(update_id))); con.commit(); return True
        finally: con.close()

    def complete_update(self, update_id: int, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._connect() as con:
            con.execute("INSERT INTO update_claims(update_id,status,lease_until,updated_at) VALUES(?,?,0,?) ON CONFLICT(update_id) DO UPDATE SET status='done',lease_until=0,updated_at=excluded.updated_at", (int(update_id), "done", now)); self._prune_transient(con, now)
    def release_update(self, update_id: int) -> None:
        with self._connect() as con: con.execute("DELETE FROM update_claims WHERE update_id=? AND status='processing'", (int(update_id),))

    def begin_flow(self, user_id: int, flow: str, ttl_seconds: int) -> None:
        with self._connect() as con: con.execute("INSERT INTO owner_flows(user_id,flow,expires_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET flow=excluded.flow,expires_at=excluded.expires_at", (user_id, flow, time.time()+ttl_seconds))
    def active_flow(self, user_id: int) -> str | None:
        with self._connect() as con:
            row = con.execute("SELECT flow,expires_at FROM owner_flows WHERE user_id=?", (user_id,)).fetchone()
            if row is None: return None
            if float(row[1]) <= time.time(): con.execute("DELETE FROM owner_flows WHERE user_id=?", (user_id,)); return None
            return str(row[0])
    def clear_flow(self, user_id: int) -> None:
        with self._connect() as con: con.execute("DELETE FROM owner_flows WHERE user_id=?", (user_id,))

    def record_question(self, user_id: int, *, success: bool, latency_ms: float, cache_hit: bool, ai_calls: int, error_class: str | None = None) -> None:
        with self._connect() as con: con.execute("INSERT INTO question_usage(created_at,user_id,success,latency_ms,cache_hit,ai_calls,error_class) VALUES(?,?,?,?,?,?,?)", (time.time(), user_id, int(success), float(latency_ms), int(cache_hit), int(ai_calls), error_class))
    def usage_summary(self) -> BotUsageSummary:
        with self._connect() as con:
            row = con.execute("SELECT count(*),coalesce(sum(success),0),coalesce(sum(CASE WHEN success=0 THEN 1 ELSE 0 END),0),coalesce(sum(cache_hit),0),coalesce(sum(ai_calls),0),coalesce(avg(latency_ms),0) FROM question_usage").fetchone(); return BotUsageSummary(*(int(row[i]) for i in range(5)), float(row[5]))

    def create_source_session(self, user_id: int, payload: list[dict], ttl_seconds: int) -> str:
        sid = secrets.token_urlsafe(8)[:12]
        with self._connect() as con:
            con.execute("DELETE FROM source_sessions WHERE expires_at<=?", (time.time(),)); con.execute("INSERT INTO source_sessions(session_id,user_id,payload_json,expires_at) VALUES(?,?,?,?)", (sid,user_id,json.dumps(payload,ensure_ascii=False,separators=(",",":")),time.time()+ttl_seconds))
        return sid
    def get_source_session(self, session_id: str, user_id: int) -> list[dict] | None:
        with self._connect() as con:
            row = con.execute("SELECT payload_json,expires_at,user_id FROM source_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None or int(row[2]) != int(user_id): return None
            if float(row[1]) <= time.time(): con.execute("DELETE FROM source_sessions WHERE session_id=?", (session_id,)); return None
            try: payload = json.loads(row[0])
            except (TypeError, json.JSONDecodeError): con.execute("DELETE FROM source_sessions WHERE session_id=?", (session_id,)); return None
            return list(payload) if isinstance(payload, list) else None

    def get_conversation_context(self, user_id: int, *, ttl_seconds: int = 6 * 3600):
        from drjavanbot.intelligence.conversation import ConversationQuestionContext
        with self._connect() as con:
            row = con.execute("SELECT payload_json,updated_at FROM conversation_semantics WHERE user_id=?", (int(user_id),)).fetchone()
            if row is None:
                return ConversationQuestionContext()
            if float(row[1]) < time.time() - max(300, int(ttl_seconds)):
                con.execute("DELETE FROM conversation_semantics WHERE user_id=?", (int(user_id),))
                return ConversationQuestionContext()
            try:
                payload = json.loads(row[0])
                if not isinstance(payload, dict): raise ValueError
                return ConversationQuestionContext.from_dict(payload)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                con.execute("DELETE FROM conversation_semantics WHERE user_id=?", (int(user_id),))
                return ConversationQuestionContext()

    def set_conversation_context(self, user_id: int, context) -> None:
        payload = json.dumps(context.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as con:
            con.execute("INSERT INTO conversation_semantics(user_id,payload_json,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at", (int(user_id), payload, time.time()))

    def last_reindex_at(self) -> str | None: return self.get_setting("last_reindex_at")
    def set_last_reindex_at(self, value: str) -> None: self.set_setting("last_reindex_at", value)
    def provider_auth_failed(self) -> bool: return self.get_setting("provider_auth") == "failed"
    def set_provider_auth_failed(self, failed: bool) -> None: self.set_setting("provider_auth", "failed" if failed else "ok")

    def _prune_transient(self, con: sqlite3.Connection, now: float) -> None:
        cutoff = now - _STATE_RETENTION_SECONDS
        con.execute("DELETE FROM update_claims WHERE status='done' AND updated_at<?", (cutoff,)); con.execute("DELETE FROM processed_updates WHERE created_at<?", (cutoff,)); con.execute("DELETE FROM rate_events WHERE created_at<?", (now - 60.0,)); con.execute("DELETE FROM source_sessions WHERE expires_at<=?", (now,)); con.execute("DELETE FROM owner_flows WHERE expires_at<=?", (now,)); con.execute("DELETE FROM conversation_semantics WHERE updated_at<?", (now - 6 * 3600,))

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA synchronous=NORMAL")
            con.executescript("""
            CREATE TABLE IF NOT EXISTS bot_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS allowed_users(user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS rate_events(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_rate_events_user_time ON rate_events(user_id,created_at);
            CREATE TABLE IF NOT EXISTS processed_updates(update_id INTEGER PRIMARY KEY,created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS update_claims(update_id INTEGER PRIMARY KEY,status TEXT NOT NULL CHECK(status IN ('processing','done')),lease_until REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_update_claims_status_time ON update_claims(status,updated_at);
            CREATE TABLE IF NOT EXISTS owner_flows(user_id INTEGER PRIMARY KEY,flow TEXT NOT NULL,expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS question_usage(id INTEGER PRIMARY KEY,created_at REAL NOT NULL,user_id INTEGER NOT NULL,success INTEGER NOT NULL,latency_ms REAL NOT NULL,cache_hit INTEGER NOT NULL,ai_calls INTEGER NOT NULL,error_class TEXT);
            CREATE TABLE IF NOT EXISTS source_sessions(session_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,payload_json TEXT NOT NULL,expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_semantics(user_id INTEGER PRIMARY KEY,payload_json TEXT NOT NULL,updated_at REAL NOT NULL);
            """)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5); con.execute("PRAGMA busy_timeout=5000"); return con
