"""The reliability layer, tested without an LLM anywhere in the picture.

These are the claims the walkthrough makes out loud, so they are the ones that need
to be mechanically true:

  * an idempotent replay returns the cached response and the vendor counter
    increments exactly once
  * the key does not change when the arguments change, because arguments come from
    a model and model output is not stable
  * a mid-saga failure compensates in reverse, and the two uncompensatable steps are
    recorded as such rather than silently skipped
  * the policy gate raises `PolicyViolation` — an exception, not a string an agent
    could rephrase against
  * a loan is reconstructible from `saga_log` alone
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meridian.core import db as db_mod  # noqa: E402
from meridian.core.errors import (  # noqa: E402
    CompensationError,
    InDoubt,
    MeridianError,
    PolicyViolation,
    VendorError,
)
from meridian.core.idempotency import idempotent, make_key, reserve  # noqa: E402
from meridian.core.events import EventBus  # noqa: E402
from meridian.core.policy import PolicyContext, assert_no_prohibited_basis, requires_state  # noqa: E402
from meridian.core.saga import Saga, SagaStep  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    conn = db_mod.Database(tmp_path / "t.db")
    db_mod.set_db(conn)
    with conn.transaction():
        conn.save_loan_state("L1", "new", {}, scenario="test")
    yield conn
    conn.close()
    db_mod.set_db(None)


# -- approvals -----------------------------------------------------------


def test_approval_is_reopened_only_when_canonical_artifact_changes(db):
    first = db.request_approval("L1", "G2", {"kind": "exception", "reasons": ["DTI"]})
    db.record_approval(
        "L1", "G2", first["artifact_digest"], "approved", "Avery Underwriter"
    )

    same = db.request_approval("L1", "G2", {"reasons": ["DTI"], "kind": "exception"})
    assert same["status"] == "approved"
    assert same["artifact_digest"] == first["artifact_digest"]

    changed = db.request_approval("L1", "G2", {"kind": "exception", "reasons": ["LTV"]})
    assert changed["status"] == "pending"
    assert changed["approver"] is None
    assert changed["artifact_digest"] != first["artifact_digest"]

    with pytest.raises(MeridianError, match="matching artifact"):
        db.record_approval(
            "L1", "G2", first["artifact_digest"], "rejected", "Morgan Reviewer"
        )
    assert db.approval("L1", "G2")["status"] == "pending"


# -- idempotency ---------------------------------------------------------


def test_event_unsubscribe_is_idempotent():
    bus = EventBus(persist=False)
    received = []
    unsubscribe = bus.subscribe(received.append)

    bus.emit("before_cleanup")
    unsubscribe()
    unsubscribe()
    bus.emit("after_cleanup")

    assert [event.kind for event in received] == ["before_cleanup"]


def test_idempotent_call_fires_once_and_replays_response(db):
    calls: list[dict] = []

    @idempotent("pull_credit")
    def pull_credit(loan_id: str, **kwargs):
        calls.append(kwargs)
        db.record_vendor_call(loan_id, "credit_bureau", "tri_merge")
        return {"score": 742, "bureaus": 3}

    first = pull_credit("L1", ssn="123-45-6789")
    second = pull_credit("L1", ssn="123-45-6789")

    assert first == second == {"score": 742, "bureaus": 3}
    assert len(calls) == 1
    assert db.vendor_call_count("credit_bureau", "tri_merge") == 1


def test_key_is_independent_of_model_supplied_arguments(db):
    """The bug this design exists to prevent.

    An agent retrying a tool call may emit "123-45-6789" and then "123456789". If the
    key hashed the arguments those are two keys and the borrower gets a second hard
    inquiry.
    """
    calls: list[dict] = []

    @idempotent("pull_credit")
    def pull_credit(loan_id: str, **kwargs):
        calls.append(kwargs)
        db.record_vendor_call(loan_id, "credit_bureau", "tri_merge")
        return {"score": 742}

    pull_credit("L1", ssn="123-45-6789")
    pull_credit("L1", ssn="123456789", bureau="experian")  # same intent, different args

    assert len(calls) == 1, "argument drift must not produce a second hard inquiry"
    assert db.vendor_call_count("credit_bureau", "tri_merge") == 1


def test_deliberate_new_epoch_permits_a_fresh_call(db):
    """Credit ages out at 120 days. A *deliberate* re-pull must be possible — the
    guarantee is 'no accidental duplicate', not 'never twice'."""
    calls = []

    @idempotent("pull_credit")
    def pull_credit(loan_id: str):
        calls.append(1)
        return {"score": 742}

    pull_credit("L1")
    with db.transaction():
        db.bump_attempt_epoch("L1")
    pull_credit("L1")

    assert len(calls) == 2


def test_routine_state_writes_preserve_the_attempt_epoch(db):
    """The epoch is bumped deliberately and by nobody else.

    `save_loan_state` runs at every step boundary. If its upsert reset the epoch, the next
    step would compute the pre-bump idempotency key, find the old response, and replay a
    credit pull the orchestrator had just decided needed re-running.
    """
    with db.transaction():
        assert db.bump_attempt_epoch("L1") == 1
    assert db.attempt_epoch("L1") == 1

    with db.transaction():
        db.save_loan_state("L1", "verified", {"anything": True})

    assert db.attempt_epoch("L1") == 1


def test_bumping_the_epoch_of_an_unknown_loan_raises(db):
    """A bump that matches no row is not a no-op — it is a caller believing it has
    authorised a fresh external call when it has authorised nothing."""
    with pytest.raises(MeridianError):
        with db.transaction():
            db.bump_attempt_epoch("does-not-exist")


def test_a_reserved_but_unfilled_key_is_in_doubt_not_a_replay(db):
    """The process died between the vendor call and the commit that records its response.

    On restart the only honest answer is "we do not know whether the inquiry fired", and
    the one thing that must not happen is a silent second hard pull.
    """
    key = make_key("L1", "pull_credit", 0)
    with db.transaction():
        reserve(db, key, "L1", "pull_credit", 0)

    @idempotent("pull_credit")
    def pull_credit(loan_id: str):
        raise AssertionError("must not re-fire an in-doubt side effect")

    with pytest.raises(InDoubt):
        pull_credit("L1")


def test_key_shape():
    a = make_key("L1", "pull_credit", 0)
    assert a == make_key("L1", "pull_credit", 0)
    assert a != make_key("L1", "pull_credit", 1)
    assert a != make_key("L2", "pull_credit", 0)
    assert a != make_key("L1", "order_appraisal", 0)


# -- saga ----------------------------------------------------------------


def _build_saga(db, fail_on: str | None = None) -> tuple[Saga, list[str]]:
    trace: list[str] = []

    def ex(name):
        def _f(loan_id, **kw):
            if fail_on == name:
                raise VendorError("amc", "appraisal came back low")
            trace.append(f"exec:{name}")
            return {"step": name}

        return _f

    def comp(name):
        def _f(loan_id, forward_result=None):
            trace.append(f"comp:{name}")
            return {"compensated": name}

        return _f

    saga = Saga("L1", db)
    saga.register(
        SagaStep(
            "pull_credit",
            ex("pull_credit"),
            compensate=None,
            compensatable=False,
            compensation_note="hard inquiry is permanent — no compensation exists",
        )
    )
    saga.register(
        SagaStep(
            "submit_to_aus",
            ex("submit_to_aus"),
            compensate=None,
            compensatable=True,
            compensation_note="read-only; nothing to compensate",
        )
    )
    saga.register(
        SagaStep(
            "order_appraisal",
            ex("order_appraisal"),
            compensate=comp("order_appraisal"),
            compensation_note="cancellable pre-inspection only",
        )
    )
    saga.register(
        SagaStep(
            "lock_rate",
            ex("lock_rate"),
            compensate=comp("lock_rate"),
            compensation_note="relock subject to worst-case pricing",
        )
    )
    return saga, trace


def test_compensation_unwinds_in_reverse_order(db):
    saga, trace = _build_saga(db)
    for step in ("pull_credit", "submit_to_aus", "order_appraisal", "lock_rate"):
        saga.run(step)

    report = saga.compensate_all(reason="LTV breach after low appraisal")
    order = [r["step"] for r in report]

    assert order == ["lock_rate", "order_appraisal", "submit_to_aus", "pull_credit"]
    assert trace[-2:] == ["comp:lock_rate", "comp:order_appraisal"]


def test_uncompensatable_step_is_recorded_not_hidden(db):
    saga, _ = _build_saga(db)
    saga.run("pull_credit")
    saga.run("lock_rate")
    report = saga.compensate_all(reason="denied")

    credit = next(r for r in report if r["step"] == "pull_credit")
    assert credit["outcome"] == "none_possible"
    assert "permanent" in credit["note"]

    rows = db.saga_history("L1")
    assert any(
        r["step"] == "pull_credit" and r["phase"] == "compensate" and r["outcome"] == "none_possible"
        for r in rows
    ), "the ledger must show that compensation was impossible, not that it was skipped"


def test_relock_carries_worst_case_pricing_note(db):
    saga, _ = _build_saga(db)
    saga.run("lock_rate")
    report = saga.compensate_all(reason="denied")
    assert "worst-case pricing" in report[0]["note"]


def test_forward_failure_leaves_prior_steps_available_for_unwind(db):
    saga, trace = _build_saga(db, fail_on="order_appraisal")
    saga.run("pull_credit")
    saga.run("submit_to_aus")
    with pytest.raises(VendorError):
        saga.run("order_appraisal")

    report = saga.compensate_all(reason="appraisal failed")
    assert [r["step"] for r in report] == ["submit_to_aus", "pull_credit"]
    assert "comp:order_appraisal" not in trace, "a step that never executed must not compensate"


def test_compensation_failure_surfaces_rather_than_being_swallowed(db):
    saga, _ = _build_saga(db)

    def boom(loan_id, forward_result=None):
        raise VendorError("pricing", "lock desk unreachable")

    saga.steps["lock_rate"].compensate = boom
    saga.run("lock_rate")
    with pytest.raises(CompensationError):
        saga.compensate_all(reason="denied")

    rows = db.saga_history("L1")
    assert any(r["phase"] == "compensate" and r["outcome"] == "error" for r in rows)


def test_saga_step_replays_instead_of_refiring(db):
    saga, trace = _build_saga(db)
    saga.run("pull_credit")
    saga2 = Saga("L1", db)
    for step in saga.steps.values():
        saga2.register(step)
    saga2.run("pull_credit")

    assert trace.count("exec:pull_credit") == 1


# -- ACID / reconstruction ----------------------------------------------


def test_transaction_rolls_back_partial_work(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.save_loan_state("L2", "underwriting", {"x": 1})
            raise RuntimeError("boom")
    assert db.load_loan_state("L2") is None


def test_nested_transactions_share_one_commit_boundary(db):
    with db.transaction():
        db.save_loan_state("L3", "a", {})
        with db.transaction():
            db.save_loan_state("L3", "b", {})
    assert db.load_loan_state("L3")["status"] == "b"

    with pytest.raises(RuntimeError):
        with db.transaction():
            db.save_loan_state("L4", "a", {})
            with db.transaction():
                db.save_loan_state("L4", "b", {})
            raise RuntimeError("outer fails")
    assert db.load_loan_state("L4") is None, "inner block must not commit independently"


def test_loan_is_reconstructible_from_saga_log_alone(db):
    """The source-of-truth claim, tested. Flow state is a cache; if it vanishes the
    ledger still tells you where the loan is."""
    saga, _ = _build_saga(db)
    saga.run("pull_credit")
    saga.run("submit_to_aus")
    saga.run("lock_rate")

    rebuilt = Saga("L1", db)
    for step in saga.steps.values():
        rebuilt.register(step)
    restored = rebuilt.restore()

    assert [r.name for r in restored] == ["pull_credit", "submit_to_aus", "lock_rate"]
    assert restored[0].result == {"step": "pull_credit"}


def test_compensated_step_is_not_restored_as_completed(db):
    saga, _ = _build_saga(db)
    saga.run("pull_credit")
    saga.run("lock_rate")
    saga.compensate_all(reason="denied")

    rebuilt = Saga("L1", db)
    for step in saga.steps.values():
        rebuilt.register(step)
    restored = rebuilt.restore()

    assert [r.name for r in restored] == ["pull_credit"], (
        "lock_rate was released; pull_credit's inquiry is still on the report"
    )


def test_a_step_re_executed_after_compensation_is_restored_as_completed(db):
    """Compensation is matched to execution by idempotency key, not by step name.

    Release the lock, bump the epoch to authorise a deliberate relock, lock again. The
    old compensation refers to the *old* key. Matching on the name alone saw it and
    dropped the live lock from the rebuilt state — a loan that is locked being
    reconstructed as a loan that is not.
    """
    saga, _ = _build_saga(db)
    saga.run("pull_credit")
    saga.run("lock_rate")
    saga.compensate_all(reason="denied")

    with db.transaction():
        db.bump_attempt_epoch("L1")
    saga.run("lock_rate")

    rebuilt = Saga("L1", db)
    for step in saga.steps.values():
        rebuilt.register(step)

    assert [r.name for r in rebuilt.restore()] == ["pull_credit", "lock_rate"]


# -- policy --------------------------------------------------------------


def test_policy_gate_raises_rather_than_returning_a_string(db):
    """A refusal returned as text is a suggestion. This must be an exception, or the
    agent simply rephrases and tries again."""

    @requires_state(intent_to_proceed=True)
    def order_appraisal(loan_id: str, policy: PolicyContext, amount: int = 600):
        return {"ordered": True, "fee": amount}

    ctx = PolicyContext("L1", {"intent_to_proceed": False})
    with pytest.raises(PolicyViolation) as exc:
        order_appraisal("L1", policy=ctx)

    assert "1026.19(e)(2)(i)(A)" in str(exc.value)
    assert not isinstance(exc.value, VendorError)

    ctx.set("intent_to_proceed", True)
    assert order_appraisal("L1", policy=ctx)["fee"] == 600


def test_missing_policy_context_fails_closed(db):
    @requires_state(intent_to_proceed=True)
    def order_appraisal(loan_id: str):
        return "ordered"

    with pytest.raises(PolicyViolation):
        order_appraisal("L1")


def test_credit_pull_is_not_gated_by_intent_to_proceed():
    """§1026.19(e)(2)(i)(B) — the bona fide credit-report fee exception. Gating this
    would be a domain error, so the absence of the decorator is asserted."""
    from meridian.vendors import credit_bureau

    assert not hasattr(credit_bureau.pull_tri_merge, "__meridian_requires__")


def test_prohibited_basis_detection():
    assert assert_no_prohibited_basis(["DTI exceeds overlay of 43%"]) == []
    hits = assert_no_prohibited_basis(["applicant marital status suggests instability"])
    assert "marital_status" in hits


def test_prohibited_basis_matches_whole_words_only():
    """A substring match reports "age" inside "average", "package" and "mortgage".

    A compliance check that fires on every file is a check nobody reads, so this is the
    regression that keeps the matcher on word boundaries.
    """
    clean = assert_no_prohibited_basis(
        [
            "bonus income is taken at the two-year average",
            "the condominium package was reviewed and the mortgage is warrantable",
        ]
    )
    assert clean == []

    assert "age" in assert_no_prohibited_basis(["denied on the basis of the applicant's age"])
    # The underscore form of a multi-word term matches spelled either way.
    assert "national_origin" in assert_no_prohibited_basis(["national origin was considered"])
