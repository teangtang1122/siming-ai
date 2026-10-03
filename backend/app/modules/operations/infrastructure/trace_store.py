"""Independent SQLite diagnostics with a bounded, non-blocking writer queue."""

from __future__ import annotations

import contextlib
import json
import queue
import sqlite3
import threading
import time
from pathlib import Path

from ..domain.context_trace import MEMORY_LIMIT, TracePolicy
from .trace_payload import encode_bounded, process_payload, redact

_DDL = """
CREATE TABLE IF NOT EXISTS traces (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, owner TEXT NOT NULL,
 scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, started REAL NOT NULL, finished REAL,
 status TEXT NOT NULL, mode TEXT NOT NULL, capture_status TEXT NOT NULL,
 correlations TEXT NOT NULL, dropped INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS trace_scope ON traces(owner, scope_kind, scope_id, cursor);
CREATE TABLE IF NOT EXISTS trace_correlations (
 trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE, value TEXT NOT NULL,
 PRIMARY KEY(trace_id, value));
CREATE INDEX IF NOT EXISTS trace_correlation_value ON trace_correlations(value);
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, trace_id TEXT NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL, value TEXT NOT NULL, UNIQUE(trace_id, sequence));
CREATE TABLE IF NOT EXISTS payloads (
 id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS policies (owner TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class TraceStore:
    encode = staticmethod(encode_bounded)

    def __init__(self, path: Path, *, max_bytes: int = 256 * 1024 * 1024) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.max_bytes = path, max_bytes
        self.lock = threading.RLock()
        self.queue_lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=1)
        schema_version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if schema_version > 2:
            self.db.close()
            raise ValueError("Unsupported diagnostics schema version")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA auto_vacuum=FULL")
        self.db.execute("PRAGMA journal_mode=DELETE")
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.db.execute(f"PRAGMA max_page_count={max_bytes // 2 // page_size:d}")
        self.db.executescript(_DDL)
        with self.db:
            self.db.execute(
                "UPDATE traces SET capture_status='interrupted', status='interrupted', "
                "finished=coalesce(finished,?) WHERE finished IS NULL "
                "OR (capture_status='interrupted' AND status='running')",
                (time.time(),),
            )
            if schema_version < 2:
                self.db.execute(
                    "INSERT OR IGNORE INTO trace_correlations SELECT traces.id, j.value "
                    "FROM traces, json_each(traces.correlations) j WHERE j.type='text'"
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO trace_correlations SELECT events.trace_id, j.value "
                    "FROM events, json_each(events.value, '$.data') j "
                    "WHERE json_extract(events.value, '$.event_type')='correlation' "
                    "AND j.type='text'"
                )
                self.db.execute("PRAGMA user_version=2")
        self.policies = {
            row["owner"]: TracePolicy.model_validate_json(row["value"])
            for row in self.db.execute("SELECT * FROM policies")
        }
        self.pending_bytes = 0
        self.dropped = 0
        self.write_errors = 0
        self.closed = False
        self.items: queue.Queue = queue.Queue(maxsize=1024)
        self.worker = threading.Thread(
            target=self._write_loop, name="context-trace-writer", daemon=True
        )
        self.worker.start()

    def policy(self, owner: str) -> TracePolicy:
        return self.policies.get(owner, TracePolicy())

    def set_policy(self, owner: str, mode: str, duration_minutes: int | None) -> dict:
        policy = self.policy(owner).model_copy(
            update={
                "mode": mode,
                "full_until": time.time() + duration_minutes * 60
                if mode == "full" and duration_minutes
                else None,
            }
        )
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO policies VALUES (?,?)", (owner, policy.model_dump_json())
            )
            self.policies[owner] = policy
        return policy.model_dump()

    def submit(self, record: dict, raw: bytes | None = None, secrets: tuple[str, ...] = ()) -> bool:
        size = len(raw or b"") + 2048
        with self.queue_lock:
            if self.closed or self.pending_bytes + size > MEMORY_LIMIT:
                self.dropped += 1
                return False
            try:
                self.items.put_nowait((record, raw, secrets, size))
                self.pending_bytes += size
                return True
            except queue.Full:
                self.dropped += 1
                return False

    def _write_loop(self) -> None:
        while not self.closed or not self.items.empty():
            try:
                item = self.items.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                self.items.task_done()
                return
            record, raw, secrets, size = item
            try:
                self._write(record, raw, secrets)
            except Exception:
                self.write_errors += 1
                with contextlib.suppress(Exception), self.lock:
                    self.db.rollback()
                    with self.db:
                        self.db.execute(
                            "UPDATE traces SET capture_status='partial', dropped=dropped+1 "
                            "WHERE id=?",
                            (record["trace_id"],),
                        )
            finally:
                with self.queue_lock:
                    self.pending_bytes -= size
                self.items.task_done()

    def _write(self, record: dict, raw: bytes | None, secrets: tuple[str, ...]) -> None:
        record = redact(record, secrets)
        data, kind, trace_id = record["data"], record["event_type"], record["trace_id"]
        content = None
        if raw is not None:
            processed = process_payload(raw, data.get("media_type", "application/json"), secrets)
            content = processed.pop("content")
            if processed.get("missing_reason") is None:
                processed.pop("missing_reason", None)
            data.update(processed)
            data.setdefault("observed_bytes", len(raw))
        with self.lock, self.db:
            if content is not None:
                self._prune()
            if kind == "trace_started":
                self._prune()
                scope = data["scope"]
                self.db.execute(
                    "INSERT INTO traces(id,owner,scope_kind,scope_id,started,status,mode,"
                    "capture_status,correlations) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        trace_id,
                        data["owner"],
                        scope["kind"],
                        scope["id"],
                        data["started_at"],
                        "running",
                        data["mode"],
                        "recording",
                        json.dumps(data["correlations"]),
                    ),
                )
            elif kind == "trace_finished":
                prior = self.db.execute(
                    "SELECT capture_status FROM traces WHERE id=?", (trace_id,)
                ).fetchone()
                if prior and prior[0] == "partial":
                    data["capture_status"] = "partial"
                self.db.execute(
                    "UPDATE traces SET status=?,finished=?,capture_status=CASE "
                    "WHEN capture_status='partial' THEN 'partial' ELSE ? END,"
                    "dropped=dropped+? WHERE id=?",
                    (
                        data["status"],
                        data["finished_at"],
                        data["capture_status"],
                        data["dropped_events"],
                        trace_id,
                    ),
                )
            elif kind == "correlation":
                self.db.execute(
                    "UPDATE traces SET correlations=json_patch(correlations,?) WHERE id=?",
                    (json.dumps(data), trace_id),
                )
            if kind in {"trace_started", "correlation"}:
                links = data["correlations"] if kind == "trace_started" else data
                self.db.executemany(
                    "INSERT OR IGNORE INTO trace_correlations VALUES (?,?)",
                    [(trace_id, value) for value in links.values() if isinstance(value, str)],
                )
            if data.get("missing_reason") not in {None, "recording_not_enabled"}:
                self.db.execute(
                    "UPDATE traces SET capture_status='partial' WHERE id=?", (trace_id,)
                )
            self.db.execute(
                "INSERT INTO events VALUES (?,?,?,?)",
                (
                    record["event_id"],
                    trace_id,
                    record["sequence"],
                    json.dumps(record, ensure_ascii=False),
                ),
            )
            if content is not None:
                self.db.execute("INSERT INTO payloads VALUES (?,?)", (record["event_id"], content))
            if kind == "trace_finished":
                self._prune()

    def _prune(self) -> None:
        self.db.execute(
            "DELETE FROM traces WHERE finished IS NOT NULL AND started<?",
            (time.time() - 7 * 86400,),
        )
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]

        # Count live pages so deletes in this transaction immediately free capacity.
        # Begin eviction below the SQLite hard cap, leaving room for a bounded payload.
        def live_bytes():
            pages = self.db.execute("PRAGMA page_count").fetchone()[0]
            free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
            return (pages - free) * page_size

        while live_bytes() > self.max_bytes * 3 // 8:
            cursor = self.db.execute(
                "SELECT cursor FROM traces WHERE finished IS NOT NULL ORDER BY cursor LIMIT 1"
            ).fetchone()
            if cursor is None:
                break
            self.db.execute("DELETE FROM traces WHERE cursor=?", (cursor[0],))

    def list_traces(
        self,
        owner: str,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        correlation_id: str | None = None,
        before: int | None = None,
        limit: int = 30,
    ) -> list[dict]:
        clauses, values = ["owner=?"], [owner]
        for field, value in (("scope_kind", scope_kind), ("scope_id", scope_id)):
            if value:
                clauses.append(f"{field}=?")
                values.append(value)
        if correlation_id:
            clauses.append(
                "EXISTS(SELECT 1 FROM trace_correlations c "
                "WHERE c.trace_id=traces.id AND c.value=?)"
            )
            values.append(correlation_id)
        if before:
            clauses.append("cursor<?")
            values.append(before)
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM traces WHERE "
                + " AND ".join(clauses)
                + " ORDER BY cursor DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [self._trace(row) for row in rows]

    @staticmethod
    def _trace(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["correlations"] = json.loads(result["correlations"])
        result["schema"] = "siming.context_trace.v1"
        result["origin"] = "server"
        return result

    def trace(self, owner: str, trace_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM traces WHERE owner=? AND id=?", (owner, trace_id)
            ).fetchone()
        return self._trace(row) if row else None

    def events(self, owner: str, trace_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        if not self.trace(owner, trace_id):
            return []
        with self.lock:
            rows = self.db.execute(
                "SELECT value FROM events WHERE trace_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?",
                (trace_id, after, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def payload(self, owner: str, trace_id: str, payload_id: str, offset: int = 0) -> dict | None:
        if not self.trace(owner, trace_id):
            return None
        with self.lock:
            row = self.db.execute(
                "SELECT substr(p.content,?,65536),length(p.content) FROM payloads p "
                "JOIN events e ON e.id=p.id WHERE e.trace_id=? AND p.id=?",
                (offset + 1, trace_id, payload_id),
            ).fetchone()
        return (
            {
                "content": row[0],
                "offset": offset,
                "next_offset": offset + len(row[0]),
                "total_characters": row[1],
            }
            if row
            else None
        )

    def clear(self, owner: str) -> int:
        with self.lock, self.db:
            return self.db.execute(
                "DELETE FROM traces WHERE owner=? AND finished IS NOT NULL", (owner,)
            ).rowcount

    def health(self, owner: str) -> dict:
        policy = self.policy(owner).model_dump()
        if policy["full_until"] and time.time() > policy["full_until"]:
            policy["mode"] = "summary"
        return {
            "policy": policy,
            "pending_bytes": self.pending_bytes,
            "dropped_events": self.dropped,
            "write_errors": self.write_errors,
        }

    def flush(self) -> None:
        self.items.join()

    def close(self) -> None:
        self.closed = True
        self.worker.join(timeout=5)
        if not self.worker.is_alive():
            self.db.close()
