"""meridian.db — the single source of truth.

CrewAI's `@persist()` writes flow state to its *own* SQLite database. So this file
is the authority and CrewAI's state is a cache: authoritative state is written to
`loan_state` inside `transaction()` at every step boundary, and any loan can be
reconstructed from `saga_log` alone.

Concurrency model, stated plainly: single process, one shared connection in WAL
mode, crews run their tasks sequentially. No distributed semantics are claimed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import MeridianError

DEFAULT_DB_PATH = Path(os.environ.get("MERIDIAN_DB", "meridian.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS loan_state (
    loan_id        TEXT PRIMARY KEY,
    scenario       TEXT,
    status         TEXT NOT NULL,
    state_json     TEXT NOT NULL,
    attempt_epoch  INTEGER NOT NULL DEFAULT 0,
    updated_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS saga_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id        TEXT NOT NULL,
    step_name      TEXT NOT NULL,
    phase          TEXT NOT NULL,      -- execute | compensate | compensate_failed
    outcome        TEXT NOT NULL,      -- ok | error | skipped | none_possible
    idempotency_key TEXT,
    detail_json    TEXT,
    ts             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key            TEXT PRIMARY KEY,
    loan_id        TEXT NOT NULL,
    step_name      TEXT NOT NULL,
    attempt_epoch  INTEGER NOT NULL,
    response_json  TEXT,
    ts             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,      -- adverse_action | loan_estimate
    reasons_json   TEXT,
    issued_on      TEXT NOT NULL,
    due_on         TEXT NOT NULL,      -- ECOA/Reg B: 30 days
    citation       TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id        TEXT,
    kind           TEXT NOT NULL,
    actor          TEXT,
    payload_json   TEXT,
    ts             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id        TEXT,
    vendor         TEXT NOT NULL,
    operation      TEXT NOT NULL,
    ts             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    loan_id        TEXT NOT NULL,
    gate           TEXT NOT NULL,
    status         TEXT NOT NULL,
    approver       TEXT,
    artifact_digest TEXT NOT NULL,
    artifact_json  TEXT NOT NULL,
    requested_at   REAL NOT NULL,
    decided_at     REAL,
    PRIMARY KEY (loan_id, gate)
);

CREATE TABLE IF NOT EXISTS intake_run_records (
    run_id         TEXT PRIMARY KEY,
    intake_id      TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    source_document_digests_json TEXT,
    terminal_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_saga_loan ON saga_log(loan_id, id);
CREATE INDEX IF NOT EXISTS ix_events_loan ON events(loan_id, id);
CREATE INDEX IF NOT EXISTS ix_approvals_status ON approvals(status, requested_at);
"""


class Database:
    """One shared WAL connection with a real transaction boundary.

    Note the deliberate departure from the reference implementation on disk
    (`swap_trading_prism`), which opens and closes a connection per tool call.
    Per-call connections cannot give you a transaction that spans a saga step, so
    the connection is held open and shared instead.
    """

    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        if self.path.parent != Path("."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # WAL gives one writer at a time, and the FastAPI process holding a connection
        # open while someone runs `run_cli.py` in a terminal is the routine case here. Without a
        # busy timeout the second writer's `BEGIN IMMEDIATE` fails instantly with
        # "database is locked" rather than waiting the second or two the other
        # transaction needs.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._migrate_approval_artifacts()
        intake_record_columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(intake_run_records)")
        }
        if "source_document_digests_json" not in intake_record_columns:
            self._conn.execute(
                "ALTER TABLE intake_run_records "
                "ADD COLUMN source_document_digests_json TEXT"
            )
        # Before digests were persisted, the migration used [] as a default. Uploaded
        # intake runs always have source documents, so that value means "not captured",
        # not an authoritative empty provenance list.
        self._conn.execute(
            "UPDATE intake_run_records SET source_document_digests_json = NULL "
            "WHERE source_document_digests_json = '[]'"
        )

    def _migrate_approval_artifacts(self) -> None:
        """Bind approvals created by older schemas to their canonical artifact."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(approvals)")}
        if "artifact_digest" not in columns:
            self._conn.execute("ALTER TABLE approvals ADD COLUMN artifact_digest TEXT")
        for row in self._conn.execute(
            "SELECT rowid, artifact_json FROM approvals WHERE artifact_digest IS NULL"
        ):
            artifact_json = self._canonical_artifact(json.loads(row["artifact_json"]))
            self._conn.execute(
                "UPDATE approvals SET artifact_json = ?, artifact_digest = ? WHERE rowid = ?",
                (artifact_json, self._artifact_digest(artifact_json), row["rowid"]),
            )

    @staticmethod
    def _canonical_artifact(artifact: dict[str, Any]) -> str:
        return json.dumps(artifact, default=str, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _artifact_digest(artifact_json: str) -> str:
        return hashlib.sha256(artifact_json.encode("utf-8")).hexdigest()

    # -- transactions ----------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic unit of work. Reentrant — nested blocks join the outer one.

        `isolation_level=None` puts sqlite3 in autocommit, so BEGIN/COMMIT are
        issued explicitly here. That is what makes "state, saga_log and the
        idempotency key commit together" a true statement rather than a diagram.
        """
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except Exception:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_intake_run_record(
        self,
        run_id: str,
        intake_id: str,
        result: dict[str, Any],
        source_document_digests: list[str],
        terminal_at: str,
    ) -> None:
        self.execute(
            """
            INSERT INTO intake_run_records (
                run_id, intake_id, result_json, source_document_digests_json, terminal_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                intake_id = excluded.intake_id,
                result_json = excluded.result_json,
                source_document_digests_json = excluded.source_document_digests_json,
                terminal_at = excluded.terminal_at
            """,
            (
                run_id,
                intake_id,
                json.dumps(result, default=str),
                json.dumps(source_document_digests),
                terminal_at,
            ),
        )

    def load_intake_run_record(self, run_id: str) -> dict[str, Any] | None:
        row = self.one(
            """
            SELECT run_id, intake_id, result_json, source_document_digests_json, terminal_at
            FROM intake_run_records WHERE run_id = ?
            """,
            (run_id,),
        )
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "intake_id": row["intake_id"],
            "result": json.loads(row["result_json"]),
            "source_document_digests": (
                json.loads(row["source_document_digests_json"])
                if row["source_document_digests_json"] is not None
                else None
            ),
            "terminal_at": row["terminal_at"],
        }

    # -- loan state ------------------------------------------------------

    def save_loan_state(
        self, loan_id: str, status: str, state: dict[str, Any], scenario: str | None = None
    ) -> None:
        """Authoritative write. Callers wrap this in `transaction()` alongside
        whatever else must commit atomically with it."""
        import time

        self.execute(
            """
            INSERT INTO loan_state (loan_id, scenario, status, state_json, attempt_epoch, updated_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT attempt_epoch FROM loan_state WHERE loan_id = ?), 0), ?)
            ON CONFLICT(loan_id) DO UPDATE SET
                status = excluded.status,
                state_json = excluded.state_json,
                scenario = COALESCE(excluded.scenario, loan_state.scenario),
                updated_at = excluded.updated_at
            """,
            (loan_id, scenario, status, json.dumps(state, default=str), loan_id, time.time()),
        )

    def load_loan_state(self, loan_id: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM loan_state WHERE loan_id = ?", (loan_id,))
        if not row:
            return None
        return {
            "loan_id": row["loan_id"],
            "scenario": row["scenario"],
            "status": row["status"],
            "attempt_epoch": row["attempt_epoch"],
            "state": json.loads(row["state_json"]),
        }

    def attempt_epoch(self, loan_id: str) -> int:
        row = self.one("SELECT attempt_epoch FROM loan_state WHERE loan_id = ?", (loan_id,))
        return int(row["attempt_epoch"]) if row else 0

    def bump_attempt_epoch(self, loan_id: str) -> int:
        """Only the orchestrator calls this, and only when it *deliberately*
        intends a new external call (e.g. credit ages out at 120 days).

        A bump against a loan with no `loan_state` row is a bug, not a no-op: the UPDATE
        matches nothing, the epoch stays at 0, and the caller believes it has authorised a
        fresh pull when it has authorised nothing. That silently reuses the previous
        attempt's idempotency key, which is the exact failure this whole mechanism exists
        to prevent — so it raises.
        """
        cur = self.execute(
            "UPDATE loan_state SET attempt_epoch = attempt_epoch + 1 WHERE loan_id = ?",
            (loan_id,),
        )
        if cur.rowcount == 0:
            raise MeridianError(
                f"cannot bump attempt_epoch for {loan_id}: no loan_state row exists"
            )
        return self.attempt_epoch(loan_id)

    # -- reconstruction --------------------------------------------------

    def saga_history(self, loan_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "step": r["step_name"],
                "phase": r["phase"],
                "outcome": r["outcome"],
                "key": r["idempotency_key"],
                "detail": json.loads(r["detail_json"]) if r["detail_json"] else {},
                "ts": r["ts"],
            }
            for r in self.query("SELECT * FROM saga_log WHERE loan_id = ? ORDER BY id", (loan_id,))
        ]

    def vendor_call_count(
        self, vendor: str, operation: str | None = None, loan_id: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) c FROM vendor_calls WHERE vendor = ?"
        params: list[Any] = [vendor]
        if operation:
            sql += " AND operation = ?"
            params.append(operation)
        if loan_id:
            sql += " AND loan_id = ?"
            params.append(loan_id)
        row = self.one(sql, tuple(params))
        return int(row["c"]) if row else 0

    def record_vendor_call(self, loan_id: str | None, vendor: str, operation: str) -> None:
        import time

        self.execute(
            "INSERT INTO vendor_calls (loan_id, vendor, operation, ts) VALUES (?, ?, ?, ?)",
            (loan_id, vendor, operation, time.time()),
        )

    # -- human approvals ------------------------------------------------

    def request_approval(self, loan_id: str, gate: str, artifact: dict[str, Any]) -> dict[str, Any]:
        """Reuse an approval only while it covers the same canonical artifact."""
        import time

        artifact_json = self._canonical_artifact(artifact)
        artifact_digest = self._artifact_digest(artifact_json)
        existing = self.one(
            "SELECT artifact_digest FROM approvals WHERE loan_id = ? AND gate = ?",
            (loan_id, gate),
        )
        if existing and existing["artifact_digest"] == artifact_digest:
            return self.approval(loan_id, gate) or {}

        self.execute(
            """
            INSERT INTO approvals
                (loan_id, gate, status, approver, artifact_digest, artifact_json,
                 requested_at, decided_at)
            VALUES (?, ?, 'pending', NULL, ?, ?, ?, NULL)
            ON CONFLICT(loan_id, gate) DO UPDATE SET
                status = 'pending',
                approver = NULL,
                artifact_digest = excluded.artifact_digest,
                artifact_json = excluded.artifact_json,
                requested_at = excluded.requested_at,
                decided_at = NULL
            """,
            (loan_id, gate, artifact_digest, artifact_json, time.time()),
        )
        return self.approval(loan_id, gate) or {}

    def record_approval(
        self, loan_id: str, gate: str, artifact_digest: str, decision: str, approver: str
    ) -> None:
        import time

        if decision not in {"approved", "rejected"}:
            raise ValueError("approval decision must be 'approved' or 'rejected'")
        if not approver.strip():
            raise ValueError("approval requires a named approver")
        cur = self.execute(
            """
            UPDATE approvals SET status = ?, approver = ?, decided_at = ?
             WHERE loan_id = ? AND gate = ? AND artifact_digest = ? AND status = 'pending'
            """,
            (decision, approver.strip(), time.time(), loan_id, gate, artifact_digest),
        )
        if cur.rowcount == 0:
            raise MeridianError(
                f"no pending approval {gate!r} matching artifact {artifact_digest!r} "
                f"exists for {loan_id}"
            )

    def approval(self, loan_id: str, gate: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM approvals WHERE loan_id = ? AND gate = ?", (loan_id, gate))
        if not row:
            return None
        return {
            "loan_id": row["loan_id"],
            "gate": row["gate"],
            "status": row["status"],
            "approver": row["approver"],
            "artifact_digest": row["artifact_digest"],
            "artifact": json.loads(row["artifact_json"]),
            "requested_at": row["requested_at"],
            "decided_at": row["decided_at"],
        }

    def approval_queue(self, status: str = "pending") -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT loan_id, gate FROM approvals WHERE status = ? ORDER BY requested_at",
            (status,),
        )
        return [
            item for r in rows if (item := self.approval(r["loan_id"], r["gate"])) is not None
        ]

    def approval_wait_seconds(self, loan_id: str) -> float:
        rows = self.query(
            """SELECT requested_at, decided_at FROM approvals
                WHERE loan_id = ? AND decided_at IS NOT NULL AND approver != 'AUTO-APPROVE'""",
            (loan_id,),
        )
        return round(
            sum(float(r["decided_at"]) - float(r["requested_at"]) for r in rows), 3
        )


# -- module-level handle -------------------------------------------------

_db: Database | None = None


def get_db(path: Path | str | None = None) -> Database:
    """The process-wide handle. `path` only takes effect on first use.

    Passing a *different* path once a handle exists used to be silently ignored, so a
    caller believing it had opened a scratch database went on writing to the live one.
    Swapping databases is `set_db` / `reset_db`; asking for a conflicting one here raises.
    """
    global _db
    if _db is None:
        _db = Database(path or DEFAULT_DB_PATH)
    elif path is not None and Path(path) != _db.path:
        raise MeridianError(
            f"database already open at {_db.path}; refusing to silently ignore a request "
            f"for {Path(path)} — use set_db()/reset_db() to swap handles"
        )
    return _db


def set_db(db: Database | None) -> None:
    """Used by tests and by `run_cli.py --fresh` to swap in a scratch database."""
    global _db
    _db = db


TABLES = (
    "intake_run_records",
    "approvals",
    "saga_log",
    "idempotency_keys",
    "notices",
    "events",
    "vendor_calls",
    "loan_state",
)


def reset_db(path: Path | str) -> Database:
    """Start from an empty database.

    **Clears the tables; never deletes the file.** Deleting looked simpler and was worse in
    two different ways, one per platform. On Windows `unlink` raises `PermissionError`
    whenever another process holds the file — routine here, since the FastAPI process keeps a
    connection open while someone runs `run_cli.py --fresh` in a terminal. On POSIX it
    does the opposite and is more dangerous: the unlink *succeeds*, the other process keeps
    writing to the now-unlinked inode, and the two silently diverge with no error anywhere.

    Truncating in a transaction has neither failure mode, and any lock error propagates
    instead of being swallowed. This is the SQLite multi-writer limitation showing through
    in miniature — the same reason Postgres is on the roadmap rather than a nice-to-have.
    """
    global _db
    if _db is not None:
        try:
            _db.close()
        except Exception:
            pass
        _db = None

    p = Path(path)
    _db = Database(p)
    with _db.transaction():
        for table in TABLES:
            _db.execute(f"DELETE FROM {table}")
        # AUTOINCREMENT counters live here and survive a DELETE, so without this the
        # ledger on a `--fresh` run starts at id 47 instead of 1. The table is created
        # lazily on the first AUTOINCREMENT insert, so it may legitimately not exist.
        try:
            _db.execute("DELETE FROM sqlite_sequence")
        except sqlite3.OperationalError:
            pass
    return _db
