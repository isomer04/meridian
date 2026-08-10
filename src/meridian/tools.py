"""The Tool Gateway. Every side effect in the system passes through this module.

Three things happen here that would otherwise be scattered across whichever roster is active:

**Error classification.** A `VendorError` is caught and returned to the agent as a
string, because a bureau timeout is worth reasoning about. A `PolicyViolation`
propagates untouched. If a compliance refusal came back as text the agent would simply
rephrase and try again, and "an agent that cannot do it" would degrade into "an agent
that was told no once".

**Tool-call accounting.** Every call is recorded in the ledger, which is what makes
tool-call precision and recall measurable rather than anecdotal. A W-2 file that calls
`calc_self_employed_income` is a precision failure, and the eval harness sees it.

**Conditional tool menus.** `tools_for_income_analyst()` builds a *filtered* menu from
typed state. The agent then picks freely from what it is given — agentic tool calling
inside a deterministically-scoped menu. The self-employed calculator is not in the
menu on a W-2 file, so refusing it is not a matter of the agent's judgment.

The rule the whole file exists to enforce: **the model chooses freely from the menu it is
handed and nothing beyond it; the Flow owns the path; and the model may never choose a
number or an irreversible side effect.**
"""

from __future__ import annotations

import functools
from contextvars import ContextVar
from typing import Any, Callable

from .calc import calc_dti, calc_ltv, calc_piti, calc_self_employed_income, calc_w2_income
from .core.errors import PolicyViolation, VendorError
from .core.events import emit
from .core.policy import PolicyContext
from .rag import critique_retrieval, research_loop, search_guidelines, verify_citation
from .vendors import amc, aus, credit_bureau, pricing, voe


class ToolLedger:
    """Records what was called, by whom. Reset per run by the flow."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, tool: str, actor: str, ok: bool, note: str = "") -> None:
        self.calls.append({"tool": tool, "actor": actor, "ok": ok, "note": note})

    def names(self) -> list[str]:
        return [c["tool"] for c in self.calls]

    def unique(self) -> set[str]:
        return {c["tool"] for c in self.calls}

    def reset(self) -> None:
        self.calls.clear()


class RunContext:
    """One flow run's ledger and current actor, in one mutable object.

    **Mutable, and deliberately so.** The obvious implementation — two `ContextVar`s, one
    reassigned per run — does not survive contact with CrewAI: `kickoff()` dispatches each
    `@listen` method as its own asyncio task, and a task gets a *copy* of the context, so a
    `ContextVar.set()` inside one flow method is invisible to the next one. `--all` proved
    it immediately, with scenario 2 reporting scenario 1's tool calls as forbidden.

    So the ContextVar holds a reference that is bound **once**, in `OriginationFlow.__init__`,
    on the thread that will call `kickoff()`. Every task spawned from there inherits the same
    object and mutates it in place. Two flows on two threads get two objects, which is the
    isolation that matters: the API's `RunCoordinator` runs each loan's flow on its own worker
    thread within one FastAPI process, and tool-call precision and recall are reported per file.
    """

    def __init__(self) -> None:
        self.ledger = ToolLedger()
        self.actor = "system"


_RUN: ContextVar[RunContext] = ContextVar("meridian_run", default=RunContext())


def bind_run_context() -> RunContext:
    """Start a fresh, isolated run. Called by the flow before it kicks off."""
    ctx = RunContext()
    _RUN.set(ctx)
    return ctx


class _LedgerProxy:
    """Module-level `LEDGER` name, run-local storage behind it.

    Kept as a proxy rather than replaced with `current_ledger()` at every call site because
    `from .tools import LEDGER` is how the flow, the judgment layer and the tests all reach
    it, and one indirection here is cheaper than a name that silently means a different
    object per run.
    """

    def _target(self) -> ToolLedger:
        return _RUN.get().ledger

    def record(self, tool: str, actor: str, ok: bool, note: str = "") -> None:
        self._target().record(tool, actor, ok, note)

    def names(self) -> list[str]:
        return self._target().names()

    def unique(self) -> set[str]:
        return self._target().unique()

    def reset(self) -> None:
        self._target().reset()

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._target().calls


LEDGER = _LedgerProxy()


def set_actor(actor: str) -> None:
    """The flow names the acting agent before handing the menu over, so ledger rows
    are attributable to a named agent rather than to 'the system'."""
    _RUN.get().actor = actor


def current_actor() -> str:
    return _RUN.get().actor


def gateway(name: str) -> Callable:
    """Wrap a callable as a gateway tool: traced, ledgered, error-classified."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            actor = current_actor()
            emit("tool.call", actor=f"{actor}→{name}")
            try:
                result = fn(*args, **kwargs)
            except PolicyViolation:
                # Deliberately not caught. This is a control, not a guardrail.
                LEDGER.record(name, actor, ok=False, note="policy_violation")
                raise
            except VendorError as exc:
                # Handed back as text so the agent can adapt: retry, try the other
                # bureau, or raise it as a condition.
                LEDGER.record(name, actor, ok=False, note=str(exc))
                emit("vendor.error", actor=f"{actor}→{name}", error=str(exc))
                return f"VENDOR ERROR: {exc}. You may retry, use an alternative source, or record this as a condition."
            LEDGER.record(name, actor, ok=True)
            return result

        wrapper.__tool_name__ = name  # type: ignore[attr-defined]
        return wrapper

    return decorator


# -- deterministic calculators (never an agent's own arithmetic) ---------


def _year_history(borrower: dict[str, Any]) -> list[dict[str, Any]]:
    """Employment history as whole documented years, from the borrower record.

    `year_history` is an explicit list when the fixture carries one; otherwise it is
    derived from `years_at_employer`, which is the field that actually means this.
    """
    explicit = borrower.get("year_history")
    if explicit:
        return list(explicit)
    years = int(float(borrower.get("years_at_employer", 0) or 0))
    return [{"year": i + 1} for i in range(years)]


@gateway("calc_w2_income")
def t_calc_w2_income(borrower: dict[str, Any]) -> dict[str, Any]:
    # `year_history` is the documented employment history, and it used to be synthesised
    # from the *length of the bonus list* — so a borrower paid no bonus was reported as
    # having two years of history regardless, and one with a single bonus year was flagged
    # as thin even with five years at the employer. It comes from the borrower record now.
    return calc_w2_income(
        base_annual=float(borrower.get("base_annual", 0)),
        year_history=_year_history(borrower),
        bonus_history=borrower.get("bonus_history"),
        overtime_history=borrower.get("overtime_history"),
        employment_gap_months=float(borrower.get("employment_gap_months", 0)),
    ).as_dict()


@gateway("calc_self_employed_income")
def t_calc_self_employed_income(borrower: dict[str, Any]) -> dict[str, Any]:
    return calc_self_employed_income(
        schedule_c_net=borrower.get("schedule_c_net"),
        k1_ordinary=borrower.get("k1_ordinary"),
        add_backs=borrower.get("add_backs"),
        deductions=borrower.get("deductions"),
    ).as_dict()


@gateway("calc_dti")
def t_calc_dti(
    monthly_qualifying_income: float, loan: dict[str, Any], liabilities: list[dict[str, Any]]
) -> dict[str, Any]:
    piti = calc_piti(
        loan_amount=float(loan["loan_amount"]),
        annual_rate_pct=float(loan["note_rate"]),
        term_years=int(loan.get("term_years", 30)),
        annual_property_tax=float(loan.get("annual_property_tax", 0)),
        annual_hazard_insurance=float(loan.get("annual_hazard_insurance", 0)),
        monthly_hoa=float(loan.get("monthly_hoa", 0)),
        monthly_mi=float(loan.get("monthly_mi", 0)),
    )
    result = calc_dti(monthly_qualifying_income, piti["total_piti"], liabilities).as_dict()
    result["piti_detail"] = piti
    return result


@gateway("calc_ltv")
def t_calc_ltv(loan: dict[str, Any], appraised_value: float | None = None) -> dict[str, Any]:
    return calc_ltv(
        loan_amount=float(loan["loan_amount"]),
        purchase_price=float(loan.get("purchase_price") or 0) or None,
        appraised_value=float(appraised_value) if appraised_value else None,
        subordinate_liens=float(loan.get("subordinate_liens", 0)),
        transaction_type=loan.get("purpose", "purchase"),
    ).as_dict()


# -- vendors -------------------------------------------------------------


@gateway("pull_tri_merge")
def t_pull_tri_merge(loan_id: str, borrower: dict[str, Any]) -> dict[str, Any]:
    return credit_bureau.pull_tri_merge(loan_id, borrower)


@gateway("submit_to_du")
def t_submit_to_du(loan_id: str, casefile: dict[str, Any]) -> dict[str, Any]:
    return aus.submit_to_du(loan_id, casefile)


@gateway("order_appraisal")
def t_order_appraisal(loan_id: str, policy: PolicyContext, property_address: str = "") -> dict[str, Any]:
    """Gated on Reg Z §1026.19(e)(2)(i)(A). A `PolicyViolation` from here reaches the
    caller, aborting the task — see `gateway`."""
    return amc.order_appraisal(loan_id, policy=policy, property_address=property_address)


@gateway("receive_appraisal")
def t_receive_appraisal(loan_id: str, order: dict[str, Any]) -> dict[str, Any]:
    return amc.receive_appraisal(loan_id, order)


@gateway("lock_rate")
def t_lock_rate(loan_id: str, loan_amount: float, days: int = 45) -> dict[str, Any]:
    return pricing.lock_rate(loan_id, loan_amount=loan_amount, days=days)


@gateway("verify_employment")
def t_verify_employment(loan_id: str, employer: str = "") -> dict[str, Any]:
    return voe.verify_employment(loan_id, employer=employer)


@gateway("verify_deposits")
def t_verify_deposits(loan_id: str) -> dict[str, Any]:
    return voe.verify_deposits(loan_id)


# -- retrieval -----------------------------------------------------------


@gateway("search_guidelines")
def t_search_guidelines(question: str, corpora: list[str] | None = None, k: int = 4) -> list[dict[str, Any]]:
    return search_guidelines(question, corpora=corpora, k=k)


@gateway("critique_retrieval")
def t_critique_retrieval(question: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    return critique_retrieval(question, hits)


@gateway("research_guidelines")
def t_research_guidelines(question: str, product: str = "conventional_conforming") -> dict[str, Any]:
    return research_loop(question, product=product)


@gateway("verify_citation")
def t_verify_citation(corpus: str, section: str, claim: str) -> dict[str, Any]:
    return verify_citation(corpus, section, claim)


# -- conditional tool menus ---------------------------------------------

ALL_TOOLS: dict[str, Callable] = {
    "calc_w2_income": t_calc_w2_income,
    "calc_self_employed_income": t_calc_self_employed_income,
    "calc_dti": t_calc_dti,
    "calc_ltv": t_calc_ltv,
    "pull_tri_merge": t_pull_tri_merge,
    "submit_to_du": t_submit_to_du,
    "order_appraisal": t_order_appraisal,
    "receive_appraisal": t_receive_appraisal,
    "lock_rate": t_lock_rate,
    "verify_employment": t_verify_employment,
    "verify_deposits": t_verify_deposits,
    "search_guidelines": t_search_guidelines,
    "critique_retrieval": t_critique_retrieval,
    "research_guidelines": t_research_guidelines,
    "verify_citation": t_verify_citation,
}

# Tools that create an irreversible external effect are Flow-owned.  This set is
# imported by the CrewAI adapter, so every agent menu is checked at construction
# time instead of relying on each crew author to remember the rule.
IRREVERSIBLE = {"pull_tri_merge", "order_appraisal", "lock_rate"}


def tools_for_income_analyst(income_type: str) -> dict[str, Callable]:
    """The conditional-tool-calling demonstration, and the reason it is honest.

    On a W-2 file the self-employed calculator is **not in the menu**. The agent is
    not being trusted to avoid it; it cannot reach it. On a self-employed file the
    W-2 calculator is absent for the same reason, and the analyst's correct move is to
    **delegate** to the specialist rather than improvise.
    """
    base = {"verify_employment": t_verify_employment, "verify_deposits": t_verify_deposits}
    if income_type == "self_employed":
        return {**base, "calc_self_employed_income": t_calc_self_employed_income}
    if income_type == "mixed":
        return {**base, "calc_w2_income": t_calc_w2_income, "calc_self_employed_income": t_calc_self_employed_income}
    return {**base, "calc_w2_income": t_calc_w2_income}
