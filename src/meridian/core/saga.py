"""Saga: forward steps with compensating actions, unwound in reverse on failure.

Two of the four steps deliberately do not restore prior state, and both are kept
rather than quietly dropped, because that is the actual lesson of the pattern:

    pull_credit     → the hard inquiry is permanent. No compensation exists.
    submit_to_aus   → read-only. No compensation needed.
    order_appraisal → cancellable, but only pre-inspection ($600 at risk after).
    lock_rate       → releasable, but a relock is subject to worst-case pricing.

A saga is not a rollback. It is a sequence of business-level apologies, and some
apologies cost money.

Scope honesty: this is one process, so strictly it is a durable workflow rather than
a distributed saga. Same pattern, one process — see docs/architecture.md.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .db import Database, get_db
from .errors import CompensationError, InDoubt, PolicyViolation
from .events import emit
from .idempotency import COMPLETE, IN_DOUBT, lookup as idem_lookup
from .idempotency import (
    lookup_state,
    make_key,
    record as idem_record,
    release as idem_release,
    reserve as idem_reserve,
)


@dataclass
class SagaStep:
    name: str
    execute: Callable[..., Any]
    compensate: Callable[..., Any] | None = None
    compensation_note: str = ""
    compensatable: bool = True


@dataclass
class StepRecord:
    name: str
    result: Any
    key: str
    ts: float = field(default_factory=time.time)


class Saga:
    """Forward-execute steps, recording each; on failure unwind in reverse.

    Each forward step is idempotent by construction: the key is
    sha256(loan_id | step_name | attempt_epoch), so a retry of the same intent
    replays the recorded response instead of calling the vendor again.
    """

    def __init__(self, loan_id: str, db: Database | None = None):
        self.loan_id = loan_id
        self.db = db or get_db()
        self.completed: list[StepRecord] = []
        self.steps: dict[str, SagaStep] = {}

    def register(self, step: SagaStep) -> None:
        self.steps[step.name] = step

    # -- forward ---------------------------------------------------------

    def run(self, step_name: str, **kwargs: Any) -> Any:
        step = self.steps[step_name]
        epoch = self.db.attempt_epoch(self.loan_id)
        key = make_key(self.loan_id, step_name, epoch)

        state, cached = lookup_state(self.db, key)
        if state == COMPLETE:
            emit(
                "saga.execute",
                actor=step_name,
                loan_id=self.loan_id,
                outcome="replayed",
                key=key[:12],
            )
            with self.db.transaction():
                self._log(step_name, "execute", "replayed", key, {"idempotent": True})
            value = cached.get("value")
            self.completed.append(StepRecord(step_name, value, key))
            return value
        if state == IN_DOUBT:
            # A previous attempt reserved this key and never came back to fill it in. The
            # step may or may not have fired; replaying "success" here would be a fiction.
            emit(
                "saga.execute",
                actor=step_name,
                loan_id=self.loan_id,
                outcome="in_doubt",
                key=key[:12],
            )
            with self.db.transaction():
                self._log(step_name, "execute", "in_doubt", key, {"idempotent": False})
            raise InDoubt(step_name, self.loan_id, key)

        emit("saga.execute", actor=step_name, loan_id=self.loan_id, outcome="start", key=key[:12])
        # Reserve before the call, in its own commit. If the process dies inside
        # `step.execute` the reservation survives and the next run reports in-doubt
        # instead of quietly re-firing an irreversible side effect.
        with self.db.transaction():
            idem_reserve(self.db, key, self.loan_id, step_name, epoch)
        try:
            result = step.execute(loan_id=self.loan_id, **kwargs)
        except PolicyViolation as exc:
            # A control, not a vendor failure. The gate refused *before* the vendor was
            # reached, so the reservation is released — the side effect provably did not
            # fire and the step must stay retryable once the precondition is met.
            with self.db.transaction():
                idem_release(self.db, key)
                self._log(step_name, "execute", "policy_violation", key, {"error": str(exc)})
            raise
        except Exception as exc:
            # Deliberately *not* released. Anything that got as far as the vendor and then
            # failed is in doubt, and the reservation is the only record that it was tried.
            with self.db.transaction():
                self._log(step_name, "execute", "error", key, {"error": str(exc)})
            raise

        # The response, the ledger entry and the idempotency key commit together.
        # This is the whole reason for a shared connection and an explicit BEGIN.
        with self.db.transaction():
            idem_record(self.db, key, self.loan_id, step_name, epoch, {"value": result})
            self._log(step_name, "execute", "ok", key, {"result": _summarize(result)})
        self.completed.append(StepRecord(step_name, result, key))
        emit("saga.execute", actor=step_name, loan_id=self.loan_id, outcome="ok")
        return result

    # -- unwind ----------------------------------------------------------

    def compensate_all(self, reason: str) -> list[dict[str, Any]]:
        """Unwind in reverse order. Never raises for a step with no compensation —
        that is recorded as `none_possible`, which is a fact about the world, not a
        bug in the saga."""
        emit("saga.compensate", actor="saga", loan_id=self.loan_id, reason=reason, steps=len(self.completed))
        report: list[dict[str, Any]] = []
        # Pop from the tail rather than iterating a snapshot: a record is removed the
        # moment its outcome is on the ledger, so if a later compensation raises, a retry
        # resumes with only the steps that are genuinely still outstanding instead of
        # compensating the already-compensated ones a second time.
        while self.completed:
            rec = self.completed[-1]
            step = self.steps[rec.name]
            if not step.compensatable or step.compensate is None:
                outcome = "none_possible" if not step.compensatable else "skipped"
                emit(
                    "saga.compensate",
                    actor=rec.name,
                    loan_id=self.loan_id,
                    outcome=outcome,
                    note=step.compensation_note,
                )
                with self.db.transaction():
                    self._log(
                        rec.name, "compensate", outcome, rec.key, {"note": step.compensation_note}
                    )
                report.append(
                    {"step": rec.name, "outcome": outcome, "note": step.compensation_note}
                )
                self.completed.pop()
                continue
            try:
                detail = step.compensate(loan_id=self.loan_id, forward_result=rec.result)
                emit(
                    "saga.compensate",
                    actor=rec.name,
                    loan_id=self.loan_id,
                    outcome="ok",
                    note=step.compensation_note,
                )
                with self.db.transaction():
                    self._log(
                        rec.name,
                        "compensate",
                        "ok",
                        rec.key,
                        {"detail": _summarize(detail), "note": step.compensation_note},
                    )
                report.append(
                    {
                        "step": rec.name,
                        "outcome": "ok",
                        "note": step.compensation_note,
                        "detail": detail,
                    }
                )
                self.completed.pop()
            except PolicyViolation:
                # Same rule as `run`: a control is never converted into something the
                # caller can shrug off as a failed compensation.
                raise
            except Exception as exc:
                emit(
                    "saga.compensate",
                    actor=rec.name,
                    loan_id=self.loan_id,
                    outcome="failed",
                    error=str(exc),
                )
                with self.db.transaction():
                    self._log(rec.name, "compensate", "error", rec.key, {"error": str(exc)})
                report.append({"step": rec.name, "outcome": "failed", "error": str(exc)})
                raise CompensationError(f"compensation for {rec.name} failed: {exc}") from exc
        return report

    # -- ledger ----------------------------------------------------------

    def _log(
        self, step: str, phase: str, outcome: str, key: str | None, detail: dict[str, Any]
    ) -> None:
        self.db.execute(
            """INSERT INTO saga_log (loan_id, step_name, phase, outcome, idempotency_key, detail_json, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (self.loan_id, step, phase, outcome, key, json.dumps(detail, default=str), time.time()),
        )

    def restore(self) -> list[StepRecord]:
        """Rebuild the completed-step list from `saga_log` alone.

        This is the demo of the source-of-truth claim: CrewAI's flow state is a
        cache, and if it is gone the loan is still reconstructible from the ledger.
        A step that was successfully compensated is not restored as completed.

        Compensation is matched to execution by **idempotency key**, not by step name.
        Those diverge as soon as an epoch is bumped: `lock_rate` released and then
        deliberately re-locked under a new epoch is a different key, and matching on the
        name alone would see the old compensation and drop the live lock from the
        rebuilt state.
        """
        history = self.db.saga_history(self.loan_id)
        latest: dict[str, dict[str, Any]] = {}
        for h in history:
            if h["phase"] not in ("execute", "compensate"):
                continue
            if h["phase"] == "execute" and h["outcome"] not in ("ok", "replayed"):
                continue
            # Only a *successful* compensation undoes a step. `none_possible` (the hard
            # inquiry) and `skipped` are statements that nothing was undone, so those
            # steps stay completed.
            if h["phase"] == "compensate" and h["outcome"] != "ok":
                continue
            # `saga_history` is ordered by id, so the last write per key wins.
            latest[h["key"] or f"__nokey__{h['step']}__{h['id']}"] = h

        out: list[StepRecord] = []
        for h in latest.values():
            if h["phase"] != "execute":
                continue
            cached = idem_lookup(self.db, h["key"]) if h["key"] else None
            value = cached.get("value") if cached else h["detail"].get("result")
            out.append(StepRecord(h["step"], value, h["key"] or "", h["ts"]))
        out.sort(key=lambda r: r.ts)
        self.completed = out
        return out


def _summarize(value: Any, limit: int = 600) -> Any:
    """Ledger entries stay readable — you should be able to reconstruct a loan by
    eye from `select * from saga_log`, and a 40KB blob defeats that."""
    if isinstance(value, dict):
        out = {k: _summarize(v, 120) for k, v in list(value.items())[:12]}
        if len(value) > 12:
            # Say so. A silently truncated ledger entry reads as a complete one.
            out["…"] = f"{len(value) - 12} more key(s) omitted"
        return out
    if isinstance(value, (list, tuple)):
        items = [_summarize(v, 120) for v in value[:6]]
        if len(value) > 6:
            items.append(f"… {len(value) - 6} more item(s) omitted")
        return items
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"
