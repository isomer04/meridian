"""Trace event bus. `run_cli.py`, the FastAPI `RunCoordinator`, and `evals/` all
consume this, which is why no orchestration logic lives in an HTTP route handler.

Events are both streamed to subscribers and persisted to `events`, so a run can be
replayed for a reviewer after the fact — "how would you debug a bad decision in
production" is answered by this table joined to `saga_log`.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .db import get_db

log = logging.getLogger(__name__)

# In-memory retention. The `events` table is the durable record; this is the buffer the
# CLI and the API's `RunCoordinator` read, and an unbounded list grows for as long as the
# process lives.
HISTORY_LIMIT = 5000


@dataclass
class Event:
    kind: str
    actor: str = "system"
    loan_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def line(self) -> str:
        """One-line human rendering used by the CLI and the API's event stream."""
        icons = {
            "flow.step": "▸",
            "agent.start": "◆",
            "agent.done": "◇",
            "tool.call": "→",
            "tool.result": "←",
            "saga.execute": "✚",
            "saga.compensate": "↩",
            "policy.violation": "⛔",
            "vendor.error": "⚠",
            "decision": "★",
            "eval": "≡",
        }
        icon = icons.get(self.kind, "·")
        bits = []
        for k, v in self.payload.items():
            if isinstance(v, (dict, list)):
                continue
            bits.append(f"{k}={v}")
        tail = ("  " + " ".join(bits)) if bits else ""
        return f"{icon} {self.kind:<18} {self.actor:<28}{tail}"


class EventBus:
    def __init__(self, persist: bool = True, history_limit: int = HISTORY_LIMIT):
        self._subscribers: list[Callable[[Event], None]] = []
        self._history: deque[Event] = deque(maxlen=history_limit)
        self.persist = persist

    def subscribe(self, fn: Callable[[Event], None]) -> Callable[[], None]:
        self._subscribers.append(fn)
        active = True

        def unsubscribe() -> None:
            """Detach once; repeated cleanup calls are harmless.

            Streaming consumers normally unsubscribe in ``finally``, but generators can
            also be closed while handling a terminal yield. Cleanup handles should be
            idempotent so that either path can safely own teardown.
            """
            nonlocal active
            if not active:
                return
            active = False
            try:
                self._subscribers.remove(fn)
            except ValueError:
                # A bus reset or another cleanup owner may already have detached it.
                pass

        return unsubscribe

    def emit(
        self,
        kind: str,
        actor: str = "system",
        loan_id: str | None = None,
        **payload: Any,
    ) -> Event:
        ev = Event(kind=kind, actor=actor, loan_id=loan_id, payload=payload)
        self._history.append(ev)
        if self.persist:
            try:
                get_db().execute(
                    "INSERT INTO events (loan_id, kind, actor, payload_json, ts) VALUES (?, ?, ?, ?, ?)",
                    (loan_id, kind, actor, json.dumps(payload, default=str), ev.ts),
                )
            except Exception:  # noqa: BLE001 - tracing must never fail a run
                # Never propagated, but never silent either: a persistently failing insert
                # is worth finding at DEBUG rather than losing the events table entirely.
                log.debug("event persistence failed for kind=%s", kind, exc_info=True)
        for fn in list(self._subscribers):
            try:
                fn(ev)
            except Exception:  # noqa: BLE001 - a bad subscriber must not fail a run
                log.debug("event subscriber %r raised", fn, exc_info=True)
        return ev

    @property
    def history(self) -> list[Event]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()


BUS = EventBus()


def emit(kind: str, actor: str = "system", loan_id: str | None = None, **payload: Any) -> Event:
    return BUS.emit(kind, actor, loan_id, **payload)
