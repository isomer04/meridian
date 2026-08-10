"""Idempotency for side effects that cannot be taken twice.

A duplicated credit pull is a second hard inquiry on the borrower's report. Scoring
models dedupe inquiries inside a 14–45 day rate-shopping window, but **the inquiry
still appears on the report**. You cannot un-ring that bell.

The key deliberately does NOT depend on model output:

    key = sha256(loan_id | saga_step_name | attempt_epoch)

An earlier draft hashed the tool arguments. But arguments come from an LLM, so a
retry emitting "123-45-6789" and then "123456789" produces a different key — and a
second inquiry, on exactly the path the guarantee exists to protect. `attempt_epoch`
increments only when the orchestrator deliberately intends a fresh external call
(credit ages out at 120 days, for example), never because a model rephrased itself.

## What is actually guaranteed

The key is **reserved before** the side effect and **filled in after** it, in two
separate commits. That gives three states, and naming the third one is the point:

    no row              → the call has not been attempted; go ahead
    row, response NULL  → *in doubt*. It was attempted; we never learned the outcome
    row, response set   → completed; replay the recorded response verbatim

Recording only on success — which is what an earlier version did — collapses the first
two states into one. A process that dies between the vendor call and the commit then
looks, on restart, exactly like a call that never happened, and the retry fires a second
hard inquiry. So the in-doubt state is surfaced as `InDoubt` rather than resolved by
guessing: guessing "it did not happen" duplicates the inquiry, guessing "it did" invents
a response, and only an operator who can ask the vendor is in a position to say.
"""

from __future__ import annotations

import functools
import hashlib
import json
import time
from typing import Any, Callable

from .db import Database, get_db
from .errors import InDoubt
from .events import emit

MISSING = "missing"
IN_DOUBT = "in_doubt"
COMPLETE = "complete"


def make_key(loan_id: str, step_name: str, attempt_epoch: int) -> str:
    raw = f"{loan_id}|{step_name}|{attempt_epoch}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def lookup_state(db: Database, key: str) -> tuple[str, dict[str, Any] | None]:
    """The three-way answer: `MISSING`, `IN_DOUBT`, or `COMPLETE` plus its response."""
    row = db.one("SELECT response_json FROM idempotency_keys WHERE key = ?", (key,))
    if row is None:
        return MISSING, None
    if row["response_json"] is None:
        return IN_DOUBT, None
    return COMPLETE, json.loads(row["response_json"])


def lookup(db: Database, key: str) -> dict[str, Any] | None:
    """Completed responses only. A reserved-but-unfilled row is **not** a hit — see
    `lookup_state`, which is what callers that must distinguish the two should use."""
    state, value = lookup_state(db, key)
    return value if state == COMPLETE else None


def reserve(
    db: Database, key: str, loan_id: str, step_name: str, attempt_epoch: int
) -> None:
    """Claim the key with a NULL response, before the side effect fires.

    `INSERT OR IGNORE` so a re-reservation of an in-doubt key does not wipe anything;
    callers check `lookup_state` first anyway.
    """
    db.execute(
        """INSERT OR IGNORE INTO idempotency_keys
           (key, loan_id, step_name, attempt_epoch, response_json, ts)
           VALUES (?, ?, ?, ?, NULL, ?)""",
        (key, loan_id, step_name, attempt_epoch, time.time()),
    )


def release(db: Database, key: str) -> None:
    """Drop a reservation whose side effect provably did not fire.

    Only for failures that happen *before* the vendor is reached — a policy control
    refusing the call, for instance. Anything that failed at or after the call is
    genuinely in doubt and must keep its reservation.
    """
    db.execute(
        "DELETE FROM idempotency_keys WHERE key = ? AND response_json IS NULL", (key,)
    )


def record(
    db: Database, key: str, loan_id: str, step_name: str, attempt_epoch: int, response: Any
) -> None:
    db.execute(
        """INSERT OR REPLACE INTO idempotency_keys
           (key, loan_id, step_name, attempt_epoch, response_json, ts)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (key, loan_id, step_name, attempt_epoch, json.dumps(response, default=str), time.time()),
    )


def idempotent(step_name: str) -> Callable:
    """Wrap a side-effecting callable so it fires at most once per attempt_epoch.

    The wrapped function must accept `loan_id` as its first positional argument or
    as a keyword. The cached response is returned verbatim on a replay, so callers
    cannot tell the difference — which is the point.

    A key reserved by an attempt that never recorded a response raises `InDoubt` rather
    than re-firing. See this module's docstring for why that is the only honest option.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            loan_id = kwargs.get("loan_id") or (args[0] if args else None)
            if not loan_id:
                raise ValueError(f"{fn.__name__} needs loan_id for idempotency")
            db = get_db()
            epoch = db.attempt_epoch(loan_id)
            key = make_key(loan_id, step_name, epoch)

            state, cached = lookup_state(db, key)
            if state == COMPLETE:
                emit(
                    "tool.result",
                    actor=step_name,
                    loan_id=loan_id,
                    idempotent="replayed",
                    key=key[:12],
                )
                return cached.get("value") if isinstance(cached, dict) else cached
            if state == IN_DOUBT:
                emit(
                    "tool.result",
                    actor=step_name,
                    loan_id=loan_id,
                    idempotent="in_doubt",
                    key=key[:12],
                )
                raise InDoubt(step_name, loan_id, key)

            # Reserve first, in its own commit, so a crash inside `fn` leaves evidence
            # that the call was attempted.
            with db.transaction():
                reserve(db, key, loan_id, step_name, epoch)
            result = fn(*args, **kwargs)
            with db.transaction():
                record(db, key, loan_id, step_name, epoch, {"value": result})
            emit("tool.result", actor=step_name, loan_id=loan_id, idempotent="fresh", key=key[:12])
            return result

        wrapper.__meridian_step__ = step_name  # type: ignore[attr-defined]
        return wrapper

    return decorator
