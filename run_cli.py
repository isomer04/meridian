#!/usr/bin/env python
"""MERIDIAN CLI runner.

    uv run python run_cli.py --scenario 1
    uv run python run_cli.py --scenario 3            # saga compensation, in reverse
    uv run python run_cli.py --scenario 4 --twice    # idempotency: bureau counter stays 1
    uv run python run_cli.py --scenario 1 --policy-attack
    uv run python run_cli.py --scenario 5 --kill-after submit_to_aus  # then re-run to resume
    uv run python run_cli.py --ledger MER-1003       # reconstruct a loan from saga_log

`run_cli.py`, `meridian.api.app` and `evals/` all consume `core/events.py`, so no
orchestration logic lives in a UI callback and all three see exactly the same run.

Judgment mode:
  --judgment stub   rule-based, no model. Default when no API key is present.
  --judgment crew   three production agents by default; `--roster demo` retains all eight.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Windows consoles default to cp1252, which cannot encode the box-drawing and arrow
# glyphs the trace uses. Demoing on Windows is the likely case, so force UTF-8 rather
# than degrading the output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from meridian.core import db as db_mod  # noqa: E402
from meridian.core.quiet import quiet_crewai  # noqa: E402
from meridian.core.errors import ApprovalRequired, ProcessKilled  # noqa: E402
from meridian.core.events import BUS, Event  # noqa: E402
from meridian.core.replay import api_key_present, resolve_mode  # noqa: E402
from meridian.models import Decision  # noqa: E402
from meridian.vendors import credit_bureau  # noqa: E402
from meridian.vendors.fixtures import all_scenarios, by_number  # noqa: E402

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
RED, GREEN, YELLOW, CYAN = "\033[31m", "\033[32m", "\033[33m", "\033[36m"


def _colour(ev: Event) -> str:
    if ev.kind in ("policy.violation", "vendor.error"):
        return RED
    if ev.kind == "saga.compensate":
        return YELLOW
    if ev.kind in ("decision",):
        return BOLD + GREEN
    if ev.kind.startswith("agent"):
        return CYAN
    return DIM


def build_judgment(kind: str, mode: str, roster: str = "production"):
    if kind == "crew":
        from meridian.crews import CrewJudgment

        return CrewJudgment(mode=mode, roster=roster)
    from meridian.judgment import StubJudgment

    return StubJudgment()


def run_scenario(
    number: int,
    judgment_kind: str = "stub",
    mode: str = "auto",
    policy_attack: bool = False,
    kill_after: str | None = None,
    quiet: bool = False,
    fresh: bool = False,
    roster: str = "production",
    auto_approve: bool = False,
):
    from meridian.flow import OriginationFlow

    scenario = by_number(number)
    if not scenario:
        raise SystemExit(f"no scenario {number} under data/scenarios/")

    if fresh:
        db_mod.reset_db("meridian.db")
    db = db_mod.get_db()
    with db.transaction():
        db.save_loan_state(scenario["loan_id"], "new", {}, scenario=scenario["name"])

    mode = resolve_mode(mode)
    if auto_approve and not quiet:
        print(f"{YELLOW}{BOLD}AUTO-APPROVE ENABLED: human gates will not pause{RESET}")
    unsubscribe = None
    if not quiet:
        # Held and released below. `--all` runs several scenarios in one process, and a
        # subscription per scenario meant the fifth run printed every line five times.
        unsubscribe = BUS.subscribe(lambda ev: print(f"{_colour(ev)}{ev.line()}{RESET}"))
        print(f"\n{BOLD}{'─' * 78}{RESET}")
        print(f"{BOLD}  SCENARIO {number} — {scenario['name']}{RESET}")
        print(f"{DIM}  {scenario['loan_id']}   judgment={judgment_kind}   llm={mode}{RESET}")
        print(f"{DIM}  {scenario['lands']}{RESET}")
        print(f"{BOLD}{'─' * 78}{RESET}\n")

    flow = OriginationFlow(
        loan_id=scenario["loan_id"],
        judgment=build_judgment(judgment_kind, mode, roster),
        mode=mode,
        inject_policy_attack=policy_attack,
        stop_after=kill_after,
        auto_approve=auto_approve,
    )
    try:
        flow.kickoff()
    except ProcessKilled as exc:
        # Scenario 5 is *about* the process dying mid-flow. Recording it and returning the
        # state written so far is the demonstration; a traceback is not.
        print(f"\n{RED}▸ {exc}{RESET}")
        print(f"{DIM}  state up to this point is committed; re-run to resume from the ledger{RESET}\n")
    except ApprovalRequired as exc:
        print(f"\n{YELLOW}APPROVAL PAUSE: {exc}{RESET}")
        print(f"{DIM}  record a named decision, then re-run to resume from the ledger{RESET}\n")
    finally:
        if unsubscribe is not None:
            unsubscribe()
    return flow.state, scenario


def _print_unpriced_note() -> None:
    """Say when a $0.00 means "no rate on file" rather than "free".

    Silently printing zero for a model whose pricing we do not have would be the sort of
    number someone repeats in a meeting.
    """
    try:
        from meridian.crews.llm import METER

        unpriced = sorted(METER.unpriced_models)
    except Exception:
        return
    if unpriced:
        print(f"{DIM}  cost shows $0 because no rate is on file for {', '.join(unpriced)} — "
              f"tokens were metered, the price was not. Add it to core/config.PRICING.{RESET}")


def print_report(st, scenario: dict) -> None:
    print(f"\n{BOLD}{'═' * 78}{RESET}")
    d = st.decision.value.upper() if st.decision else "NONE"
    tint = RED if st.decision == Decision.DENIED else GREEN
    print(f"{BOLD}  DECISION: {tint}{d}{RESET}")
    print(f"{BOLD}{'═' * 78}{RESET}")
    print(f"\n{st.decision_rationale}\n")

    if st.overlay_conflicts:
        print(f"{BOLD}Overlay conflicts — DU said one thing, the overlay says another:{RESET}")
        for c in st.overlay_conflicts:
            mark = "exception granted" if c.get("exception_granted") else "BINDS"
            print(f"  · {c['dimension']}: actual {c['actual']} | agency: {c['agency']}")
            print(f"      overlay: {c['overlay']} [{c['citation']}] → {mark}")
        print()

    if st.conditions:
        print(f"{BOLD}Conditions ({len(st.conditions)}):{RESET}")
        for c in st.conditions:
            print(f"  [{c.kind.value:<15}] {c.description[:96]}")
            if c.citation:
                print(f"{DIM}                    ← {c.citation}{RESET}")
        print()

    total = len(st.citations)
    ok = sum(1 for c in st.citations if c.verified)
    bad = [c for c in st.citations if c.verified is False]
    print(f"{BOLD}Citation verification: {ok}/{total} verified{RESET}")
    for c in bad:
        print(f"  {RED}✗ {c.corpus}:{c.section} — {c.qc_note}{RESET}")
    if st.qc_findings:
        print(f"\n{BOLD}QC findings:{RESET}")
        for f in st.qc_findings:
            colour = RED if f["severity"] in ("critical", "high") else DIM
            print(f"  {colour}[{f['severity']:<8}] {f['kind']}: {f['detail'][:90]}{RESET}")

    if st.saga_report:
        print(f"\n{BOLD}{YELLOW}Saga compensation — unwound in reverse:{RESET}")
        for i, r in enumerate(st.saga_report, 1):
            icon = {"ok": "↩", "none_possible": "⛔", "skipped": "·"}.get(r["outcome"], "?")
            print(f"  {i}. {icon} {r['step']:<18} {r['outcome']}")
            print(f"{DIM}       {r['note']}{RESET}")
            if r.get("detail", {}).get("relock_rate_if_reapplied"):
                dt = r["detail"]
                print(
                    f"{YELLOW}       original {dt['original_rate']}% → relock at "
                    f"{dt['relock_rate_if_reapplied']}% (worst-case pricing){RESET}"
                )
            if r.get("detail", {}).get("unrecovered_cost"):
                print(f"{YELLOW}       ${r['detail']['unrecovered_cost']} NOT recovered{RESET}")

    # A file that never became an application short-circuits before the cycle-time model
    # is built, so there is nothing to report — printing a table of `None` would be worse
    # than printing nothing.
    ct = st.cycle_time
    if ct.get("scope"):
        print(f"\n{BOLD}Cycle time — {ct.get('scope')}{RESET}")
        print(f"{DIM}  {ct.get('basis')}{RESET}")
        print(f"  baseline           {ct.get('baseline_days')} days   ({ct.get('baseline_touch_minutes')} touch-min)")
        print(f"  modeled            {ct.get('modeled_days')} days")
        print(f"  irreducible floor  {ct.get('irreducible_floor_days')} days  ← does not compress: "
              f"{', '.join(ct.get('does_not_compress') or ['—'])}")
        print(f"  underwriter touches {ct.get('baseline_underwriter_touches')} → {ct.get('modeled_underwriter_touches')}")
    else:
        print(f"\n{DIM}No cycle-time model — the file did not reach a decision.{RESET}")

    print(f"\n{BOLD}Per-agent cost and latency:{RESET}")
    for r in st.agent_runs:
        tools = ",".join(r.tools_called) or "—"
        print(f"  {r.agent:<32} {r.seconds:>7.3f}s  ${r.usd:>7.4f}  tools: {tools[:44]}")
    print(f"{DIM}  total {st.total_seconds()}s  ${st.total_usd()}{RESET}")
    _print_unpriced_note()

    exp = scenario.get("expected", {})
    print(f"\n{BOLD}Against expected:{RESET}")
    checks = [
        ("decision", exp.get("decision"), st.decision.value if st.decision else None),
        ("delegated_to_specialist", exp.get("delegated_to_specialist"), st.delegated_to_specialist),
        ("value_acceptance_exercised", exp.get("value_acceptance_exercised"), bool(st.value_acceptance_exercised)),
        ("appraisal_ordered", exp.get("appraisal_ordered"), "order_appraisal" in st.tool_calls),
        ("overlay_conflicts", exp.get("overlay_conflicts"), len(st.overlay_conflicts)),
    ]
    for name, want, got in checks:
        if want is None:
            continue
        ok_ = want == got
        print(f"  {GREEN if ok_ else RED}{'✓' if ok_ else '✗'}{RESET} {name:<28} expected {want!r}, got {got!r}")
    forbidden = [t for t in exp.get("tools_forbidden", []) if t in st.tool_calls]
    print(f"  {RED if forbidden else GREEN}{'✗' if forbidden else '✓'}{RESET} "
          f"{'forbidden tools called: ' + ','.join(forbidden) if forbidden else 'no forbidden tool was called'}")
    print()


def show_ledger(loan_id: str) -> None:
    """The audit story, demonstrated. If you cannot reconstruct the loan by hand from
    this table, the audit story is not real."""
    db = db_mod.get_db()
    rows = db.saga_history(loan_id)
    if not rows:
        raise SystemExit(f"no saga_log rows for {loan_id}")
    print(f"\n{BOLD}saga_log for {loan_id} — the loan, reconstructed from the ledger alone{RESET}\n")
    print(f"  {'id':>3}  {'step':<18} {'phase':<12} {'outcome':<16} key")
    for r in rows:
        print(f"  {r['id']:>3}  {r['step']:<18} {r['phase']:<12} {r['outcome']:<16} {(r['key'] or '')[:12]}")
    state = db.load_loan_state(loan_id)
    print(f"\n  loan_state.status = {state['status']}   attempt_epoch = {state['attempt_epoch']}")
    notices = db.query("SELECT * FROM notices WHERE loan_id = ?", (loan_id,))
    for n in notices:
        print(f"  notice: {n['kind']} issued {n['issued_on']} due {n['due_on']}  [{n['citation']}]")
    print(f"  credit bureau tri-merge calls: {db.vendor_call_count('credit_bureau', 'tri_merge')}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="MERIDIAN — agentic loan origination")
    ap.add_argument("--scenario", type=int, help="1-5")
    ap.add_argument("--all", action="store_true", help="run every scenario")
    ap.add_argument("--judgment", choices=["stub", "crew"], default=None)
    ap.add_argument(
        "--roster", choices=["production", "demo"], default="production",
        help="crew roster: three production agents or the eight-agent demonstration",
    )
    ap.add_argument(
        "--auto-approve",
        action="store_true",
        help="loud demo/eval mode: automatically approve every applicable human gate",
    )
    ap.add_argument("--mode", choices=["auto", "replay", "record", "live"], default="auto")
    ap.add_argument("--replay", action="store_true", help="shorthand for --mode replay")
    ap.add_argument("--twice", action="store_true", help="submit the same file twice (idempotency)")
    ap.add_argument("--policy-attack", action="store_true", help="inject an attempt to order the appraisal pre-disclosure")
    ap.add_argument("--kill-after", help="saga step after which to kill the process (scenario 5)")
    ap.add_argument("--ledger", help="print saga_log for a loan id and exit")
    ap.add_argument("--fresh", action="store_true", help="drop meridian.db first")
    ap.add_argument("--json", action="store_true", help="dump final state as JSON")
    ap.add_argument("--verbose-crewai", action="store_true", help="show CrewAI's own panels")
    args = ap.parse_args()
    quiet_crewai(not args.verbose_crewai)

    if args.ledger:
        show_ledger(args.ledger)
        return

    mode = "replay" if args.replay else args.mode
    judgment = args.judgment or ("crew" if api_key_present() else "stub")
    if judgment == "stub" and not args.judgment:
        print(f"{DIM}no API key found — running with --judgment stub (rules, no model){RESET}")

    numbers = [s["scenario_id"] for s in all_scenarios()] if args.all else [args.scenario or 1]

    for n in numbers:
        st, scenario = run_scenario(
            n,
            judgment_kind=judgment,
            mode=mode,
            policy_attack=args.policy_attack,
            kill_after=args.kill_after,
            fresh=args.fresh and n == numbers[0],
            roster=args.roster,
            auto_approve=args.auto_approve,
        )
        if st.pending_approval is not None:
            continue

        print_report(st, scenario)

        if args.twice:
            print(f"{BOLD}{'─' * 78}\n  SECOND SUBMISSION — same loan, same file\n{'─' * 78}{RESET}\n")
            before = credit_bureau.inquiry_count(scenario["loan_id"])
            st2, _ = run_scenario(
                n,
                judgment_kind=judgment,
                mode=mode,
                roster=args.roster,
                auto_approve=args.auto_approve,
            )
            after = credit_bureau.inquiry_count(scenario["loan_id"])
            print(f"\n{BOLD}Idempotency result{RESET}")
            print(f"  tri-merge calls before second run: {before}")
            print(f"  tri-merge calls after  second run: {after}")
            ok = after == 1
            print(f"  {GREEN if ok else RED}{'✓' if ok else '✗'}{RESET} bureau call counter reads "
                  f"{after} — the key derives from (loan_id | saga_step | attempt_epoch), "
                  f"not from the arguments the model produced\n")

        if args.json:
            print(json.dumps(st.model_dump(mode="json"), indent=2, default=str))


if __name__ == "__main__":
    main()
