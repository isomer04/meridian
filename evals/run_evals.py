#!/usr/bin/env python
"""Eval harness — how do you know it works?

    uv run python evals/run_evals.py
    uv run python evals/run_evals.py --judgment crew --mode replay
    uv run python evals/run_evals.py --variance-runs 10

Testing a non-deterministic system is the hard problem of this field, and it is the one
most demos skip. Five metrics:

  1. **Decision accuracy** — final decision vs. the expected label. Table stakes.
  2. **Citation validity rate** — the share of cited sections that exist *and* support the
     claim. This is a hallucination rate that was actually measured.
  3. **Handoff correctness** — on self-employed files, did `income_analyst_agent` really
     delegate? Tests the handoff as a measurable behaviour rather than an anecdote.
  4. **Tool-call precision / recall** — did it call what it should (recall) and avoid what
     it shouldn't (precision)? A W-2 file that touches `calc_self_employed_income` is a
     precision failure. This is conditional tool calling, quantified.
  5. **Run-to-run variance** — same file, N runs, how often the same decision?

On that last one, read the report honestly. Under `--judgment stub` variance is 100% by
construction and the number proves nothing about the agents; it proves the *orchestration*
is deterministic, which is worth knowing separately. The metric only measures model
non-determinism under `--judgment crew --mode live`. The report says so in place rather
than letting a 10/10 imply something it doesn't.

Cost and latency are aggregated per agent across evaluated runs, because "which agent is
expensive?" is a real operating question and the numbers are free.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from meridian.core import db as db_mod  # noqa: E402
from meridian.core.events import BUS  # noqa: E402
from meridian.core.quiet import quiet_crewai  # noqa: E402
from meridian.core.config import configured_models  # noqa: E402
from meridian.core.replay import api_key_present, resolve_mode  # noqa: E402
from meridian.models import Decision  # noqa: E402
from meridian.vendors import fixtures  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.json"
REPORT = Path(__file__).parent / "report.md"


# -- golden set assembly -------------------------------------------------


def set_path(obj: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for p in parts[:-1]:
        obj = obj[p]
    obj[parts[-1]] = value


def build_cases(spec: dict) -> list[dict]:
    """Materialise each case, applying dotted-path patches onto its base scenario."""
    cases = []
    for case in spec["cases"]:
        base = deepcopy(fixtures.by_number(case["base_scenario"]))
        if base is None:
            raise SystemExit(f"{case['id']}: no scenario {case['base_scenario']} on disk")
        for dotted, value in (case.get("patch") or {}).items():
            set_path(base, dotted, value)
        if case.get("expected"):
            base["expected"] = {**base.get("expected", {}), **case["expected"]}
        base["_case_id"] = case["id"]
        base["_note"] = case.get("note", "")
        # Cassettes are keyed by the scenario *name*, which `CrewJudgment.bind()` reads
        # off flow state. Every variant of scenario 1 inherited the same name, so V1
        # replayed V4's recording and the retrieval numbers described a file that was
        # never run. The case id is the unique thing here, so it is what names the run.
        base["_base_name"] = base.get("name", "")
        base["name"] = case["id"]
        fixtures.register(base)
        cases.append(base)
    return cases


# -- one run -------------------------------------------------------------


def run_case(
    case: dict,
    judgment_kind: str,
    mode: str,
    work_dir: Path,
    tag: str = "",
    roster: str = "production",
) -> dict:
    """Run one file end to end against a scratch database and collect everything."""
    from meridian.flow import OriginationFlow

    db_path = work_dir / f"{case['loan_id']}{tag}.db"
    db_mod.reset_db(db_path)
    db = db_mod.get_db()
    with db.transaction():
        db.save_loan_state(case["loan_id"], "new", {}, scenario=case["name"])

    if judgment_kind == "crew":
        from meridian.crews import CrewJudgment

        judgment: Any = CrewJudgment(mode=mode, roster=roster)
    else:
        from meridian.judgment import StubJudgment

        judgment = StubJudgment()

    BUS.clear()
    t0 = time.perf_counter()
    error = None
    try:
        flow = OriginationFlow(
            loan_id=case["loan_id"], judgment=judgment, mode=mode, auto_approve=True
        )
        flow.kickoff()
        st = flow.state
    except Exception as exc:  # a crashed run is a failed case, not a crashed harness
        error = f"{type(exc).__name__}: {exc}"
        st = None
    wall = time.perf_counter() - t0

    if st is None:
        return {"case_id": case["_case_id"], "error": error, "wall_seconds": round(wall, 3)}

    retrieval_observed = not (judgment_kind == "crew" and roster == "demo")
    result = {
        "case_id": case["_case_id"],
        "note": case.get("_note", ""),
        "loan_id": case["loan_id"],
        "error": None,
        "wall_seconds": round(wall, 3),
        "expected": case.get("expected", {}),
        "decision": st.decision.value if st.decision else None,
        "income_type": st.income_type.value if st.income_type else None,
        "delegated_to_specialist": st.delegated_to_specialist,
        "value_acceptance_exercised": bool(st.value_acceptance_exercised),
        "appraisal_ordered": "order_appraisal" in st.tool_calls,
        "overlay_conflicts": len(st.overlay_conflicts),
        "tool_calls": list(dict.fromkeys(st.tool_calls)),
        "citations": [
            {"cite": f"{c.corpus}:{c.section}", "verified": bool(c.verified), "note": c.qc_note}
            for c in st.citations
        ],
        "qc_findings": st.qc_findings,
        "behavior_findings": st.behavior_findings,
        "qc_passed": st.qc_passed,
        "agent_runs": [
            {"agent": r.agent, "seconds": r.seconds, "usd": r.usd, "tools": r.tools_called}
            for r in st.agent_runs
        ],
        "retrieval_observed": retrieval_observed,
        "cycle_time": st.cycle_time,
        "errors": st.errors,
    }
    if retrieval_observed:
        # The cap is per query; a file asks several queries.
        result.update({
            "retrieval_rounds_total": sum(f.get("rounds_used", 0) for f in st.guideline_findings),
            "retrieval_queries": len(st.guideline_findings),
            "retrieval_rounds_max_per_query": max(
                (f.get("rounds_used", 0) for f in st.guideline_findings), default=0
            ),
            "retrieval_sufficient": all(
                f.get("sufficient", False) for f in st.guideline_findings
            ) if st.guideline_findings else None,
        })
    return result


# -- metrics -------------------------------------------------------------


def metric_decision_accuracy(runs: list[dict]) -> dict:
    scored = [r for r in runs if r.get("expected", {}).get("decision")]
    hits = [r for r in scored if r.get("decision") == r["expected"]["decision"]]
    return {
        "n": len(scored),
        "correct": len(hits),
        "rate": round(len(hits) / len(scored), 4) if scored else None,
        "misses": [
            {"case": r["case_id"], "expected": r["expected"]["decision"], "got": r.get("decision")}
            for r in scored
            if r.get("decision") != r["expected"]["decision"]
        ],
    }


def metric_citation_validity(runs: list[dict], semantic: bool = False) -> dict:
    total = sum(len(r.get("citations", [])) for r in runs)
    ok = sum(1 for r in runs for c in r.get("citations", []) if c["verified"])
    bad = [
        {"case": r["case_id"], "cite": c["cite"], "why": c["note"]}
        for r in runs
        for c in r.get("citations", [])
        if not c["verified"]
    ]
    return {
        "cited": total,
        "verified": ok,
        "rate": round(ok / total, 4) if total else None,
        "hallucination_rate": round(1 - ok / total, 4) if total else None,
        "failures": bad,
        "ceiling": (
            "existence and lexical support followed by an LLM entailment judge that can only "
            "downgrade. This catches logical inversions, but the judge can still make a false "
            "rejection or miss a subtle contradiction; corpus accuracy is out of scope."
            if semantic
            else "deterministic existence and lexical support only. Stub runs do not invoke "
            "the LLM entailment judge, so logical inversions remain outside this number."
        ),
    }


def metric_handoff_correctness(runs: list[dict]) -> dict:
    """The handoff, tested as behaviour. Both directions matter: a self-employed file must
    delegate, and a W-2 file must not."""
    relevant = [r for r in runs if r.get("expected", {}).get("delegated_to_specialist") is not None]
    correct = [
        r
        for r in relevant
        if bool(r.get("delegated_to_specialist")) == bool(r["expected"]["delegated_to_specialist"])
    ]
    se = [r for r in relevant if r["expected"]["delegated_to_specialist"]]
    w2 = [r for r in relevant if not r["expected"]["delegated_to_specialist"]]
    return {
        "n": len(relevant),
        "correct": len(correct),
        "rate": round(len(correct) / len(relevant), 4) if relevant else None,
        "self_employed_files": len(se),
        "self_employed_delegated": sum(1 for r in se if r.get("delegated_to_specialist")),
        "w2_files": len(w2),
        "w2_wrongly_delegated": sum(1 for r in w2 if r.get("delegated_to_specialist")),
        "misses": [
            {
                "case": r["case_id"],
                "expected_delegation": r["expected"]["delegated_to_specialist"],
                "got": r.get("delegated_to_specialist"),
            }
            for r in relevant
            if bool(r.get("delegated_to_specialist")) != bool(r["expected"]["delegated_to_specialist"])
        ],
    }


def metric_tool_calls(runs: list[dict]) -> dict:
    """Precision penalises a tool that should not have been called; recall penalises one
    that should have been and wasn't. Reported separately because they mean different
    things: a recall miss is usually an incomplete file, a precision miss is an agent
    reaching for something it had no business touching."""
    tp = fp = fn = 0
    per_case = []
    for r in runs:
        exp = r.get("expected", {})
        want = set(exp.get("tools_expected", []) or [])
        forbid = set(exp.get("tools_forbidden", []) or [])
        got = set(r.get("tool_calls", []) or [])
        hit = want & got
        missed = want - got
        violated = forbid & got
        tp += len(hit)
        fn += len(missed)
        fp += len(violated)
        if missed or violated:
            per_case.append(
                {
                    "case": r["case_id"],
                    "missed_expected": sorted(missed),
                    "called_forbidden": sorted(violated),
                }
            )
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall and (precision + recall)
        else None
    )
    return {
        "true_positives": tp,
        "false_positives_forbidden_calls": fp,
        "false_negatives_missed_calls": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "problems": per_case,
    }


def metric_variance(
    spec: dict,
    cases: list[dict],
    judgment_kind: str,
    mode: str,
    work_dir: Path,
    runs_n: int,
    roster: str = "production",
) -> dict:
    """Same file, N runs. The sharpest metric, and the one to read most carefully."""
    ids = set(spec.get("variance_cases", []))
    targets = [c for c in cases if c["_case_id"] in ids]
    out = []
    for case in targets:
        decisions, seconds = [], []
        for i in range(runs_n):
            r = run_case(case, judgment_kind, mode, work_dir, tag=f"-v{i}", roster=roster)
            decisions.append(r.get("decision") or f"ERROR:{r.get('error')}")
            seconds.append(r["wall_seconds"])
        counts = Counter(decisions)
        top, n_top = counts.most_common(1)[0]
        out.append(
            {
                "case": case["_case_id"],
                "runs": runs_n,
                "modal_decision": top,
                "agreement": f"{n_top}/{runs_n}",
                "agreement_rate": round(n_top / runs_n, 4),
                "distinct_decisions": dict(counts),
                "seconds_median": round(statistics.median(seconds), 3),
                "seconds_spread": round(max(seconds) - min(seconds), 3),
            }
        )
    deterministic = all(r["agreement_rate"] == 1.0 for r in out) if out else None
    return {
        "cases": out,
        "all_deterministic": deterministic,
        "interpretation": (
            "Under --judgment stub this is 100% BY CONSTRUCTION and says nothing about "
            "agent non-determinism — it says the orchestration is deterministic, which is a "
            "separate and useful fact. Model non-determinism is only measured under "
            "--judgment crew --mode live."
            if judgment_kind == "stub"
            else (
                "Measured against the crews. Under --mode replay the model's tokens come from "
                "cassettes, so this still measures the orchestration rather than the model; "
                "run --mode live for the number that includes model non-determinism."
                if mode != "live"
                else "Measured against live model calls. This is the real number."
            )
        ),
    }


def metric_cost_latency(runs: list[dict]) -> dict:
    per_agent: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "seconds": 0.0, "usd": 0.0, "tools": Counter()}
    )
    for r in runs:
        for a in r.get("agent_runs", []):
            slot = per_agent[a["agent"]]
            slot["calls"] += 1
            slot["seconds"] += a["seconds"]
            slot["usd"] += a["usd"]
            for t in a["tools"]:
                slot["tools"][t] += 1
    table = [
        {
            "agent": name,
            "invocations": v["calls"],
            "total_seconds": round(v["seconds"], 4),
            "mean_seconds": round(v["seconds"] / v["calls"], 4) if v["calls"] else 0,
            "total_usd": round(v["usd"], 6),
            "tools": dict(v["tools"]),
        }
        for name, v in sorted(per_agent.items(), key=lambda kv: -kv[1]["seconds"])
    ]
    return {
        "per_agent": table,
        "run_total_seconds": round(sum(r["wall_seconds"] for r in runs), 3),
        "run_total_usd": round(sum(a["usd"] for r in runs for a in r.get("agent_runs", [])), 6),
    }


def metric_retrieval(runs: list[dict]) -> dict:
    """Not one of the five headline metrics, but free to collect and it is the thing that
    tells you whether the critique loop is doing any work or just burning a round."""
    observed = [r for r in runs if r.get("retrieval_observed")]
    if not observed:
        return {
            "scored": False,
            "mean_queries_per_file": "not scored",
            "mean_rounds_per_query": "not scored",
            "max_rounds_on_any_query": "not scored",
            "cap": "not scored",
            "files_all_queries_sufficient": "not scored",
        }
    totals = [r["retrieval_rounds_total"] for r in observed if r.get("retrieval_rounds_total")]
    queries = [r["retrieval_queries"] for r in observed if r.get("retrieval_queries")]
    per_query_max = [
        r["retrieval_rounds_max_per_query"] for r in observed if r.get("retrieval_rounds_max_per_query")
    ]
    suff = [r["retrieval_sufficient"] for r in observed if r.get("retrieval_sufficient") is not None]
    all_rounds = totals or [0]
    all_q = sum(queries) or 1
    return {
        "scored": True,
        "mean_queries_per_file": round(statistics.mean(queries), 2) if queries else None,
        "mean_rounds_per_query": round(sum(all_rounds) / all_q, 2),
        "max_rounds_on_any_query": max(per_query_max) if per_query_max else None,
        "cap": 3,
        "files_all_queries_sufficient": f"{sum(1 for s in suff if s)}/{len(suff)}" if suff else None,
    }


# -- report --------------------------------------------------------------


def write_report(result: dict) -> str:
    m = result["metrics"]
    L: list[str] = []
    a = L.append

    a("# MERIDIAN — evaluation report")
    a("")
    a(f"*Generated by `evals/run_evals.py`. Judgment: **{result['judgment']}**. "
      f"Roster: **{result['roster']}**. LLM mode: **{result['mode']}**. "
      f"Embedder: **{result['embedder']}**. Human gates: **AUTO-APPROVED**.*")
    a("")
    a(f"{result['n_cases']} golden-set files, {result['variance_runs']} runs per variance case.")
    a("")

    if result["judgment"] == "stub":
        a("> ## ⚠️ Read this before believing the numbers")
        a(">")
        a("> This run used **`--judgment stub`**: the deterministic rule implementation, not the")
        a("> configured LLM roster. The stub's overlay rules and this golden set's expected labels were")
        a("> **authored by the same person in the same sitting**, so a 100% decision accuracy here")
        a("> substantially measures *self-consistency*, not correctness. It is a regression")
        a("> harness, and a good one — it will catch a broken router, a saga that stops")
        a("> compensating, an off-by-one in an overlay threshold. It is not evidence that the")
        a("> agents make good decisions.")
        a("> **Handoff correctness is also tautological under the stub**: its 'handoff' is an")
        a("> `if income_type in (...)` branch, not an observed agent delegation. It is suppressed")
        a("> below rather than presented as a result. Citation validity remains useful, but this")
        a("> run exercises only the deterministic existence/lexical floor, not the LLM entailment judge.")
        a(">")
        a("> What **is** load-bearing even on this run, because none of it is circular:")
        a(">")
        a("> - **Citation validity** — checked against the guideline corpus by a function that has")
        a(">   no knowledge of the expected labels. A wrong threshold or a fabricated section")
        a(">   fails regardless of what the golden set says.")
        a("> - **Tool-call precision** — the forbidden-tool list is a property of the file, not of")
        a(">   the answer. `V5-not-an-application` reaching the credit bureau would fail even if")
        a(">   it returned the right decision.")
        a("> - **Run-to-run determinism of the orchestration** — see the caveat in §5.")
        a(">")
        a("> For numbers that measure *judgment*, run:")
        a(">")
        a("> ```")
        a("> uv run python evals/run_evals.py --judgment crew --mode live   # needs DEEPSEEK_API_KEY")
        a("> ```")
        a(">")
        a("> The comparison between the two is the useful artifact: where crew and stub disagree,")
        a("> the golden set is the referee, and the disagreements are where the interesting")
        a("> conversation is.")
        a("")

    a("## Headline")
    a("")
    a("| Metric | Result |")
    a("|---|---|")
    da, cv, ho, tc = m["decision_accuracy"], m["citation_validity"], m["handoff_correctness"], m["tool_calls"]
    a(f"| Decision accuracy | **{_pct(da['rate'])}** ({da['correct']}/{da['n']}) |")
    a(f"| Citation validity | **{_pct(cv['rate'])}** ({cv['verified']}/{cv['cited']} cited sections) |")
    a(f"| Measured hallucination rate | **{_pct(cv['hallucination_rate'])}** |")
    if result["judgment"] == "crew" and result["roster"] == "demo":
        a(f"| Handoff correctness | **{_pct(ho['rate'])}** ({ho['correct']}/{ho['n']}) |")
    else:
        a("| Handoff correctness | **not scored** — no observed agent handoff in this roster |")
    a(f"| Tool-call precision | **{_pct(tc['precision'])}** |")
    a(f"| Tool-call recall | **{_pct(tc['recall'])}** |")
    a(f"| Run-to-run agreement | **{_variance_headline(m['variance'])}** |")
    a("")

    a("## 1. Decision accuracy")
    a("")
    a("| Case | Expected | Got | |")
    a("|---|---|---|---|")
    for r in result["runs"]:
        exp = r.get("expected", {}).get("decision")
        got = r.get("decision")
        if not exp:
            continue
        a(f"| `{r['case_id']}` | {exp} | {got or '—'} | {'✅' if exp == got else '❌'} |")
    if da["misses"]:
        a("")
        a("Misses:")
        for x in da["misses"]:
            a(f"- `{x['case']}` — expected `{x['expected']}`, got `{x['got']}`")
    a("")

    a("## 2. Citation validity")
    a("")
    a(f"{cv['verified']} of {cv['cited']} cited sections exist **and** their text supports the "
      f"claim made against them — a measured hallucination rate of **{_pct(cv['hallucination_rate'])}**.")
    a("")
    a("Existence and support are checked separately because they fail differently. A "
      "fabricated section identifier is the obvious failure. A real section cited for "
      "something it does not say is the dangerous one, because it survives a spot check.")
    a("")
    a(f"**Ceiling on this number.** {cv['ceiling']}")
    if cv["failures"]:
        a("")
        a("Failures:")
        for x in cv["failures"]:
            a(f"- `{x['case']}` — `{x['cite']}`: {x['why']}")
    a("")

    a("## 3. Handoff correctness")
    a("")
    if result["judgment"] != "crew" or result["roster"] != "demo":
        a("Not scored. Only the eight-agent demo roster contains an agent-to-agent handoff; the ")
        a("production roster dispatches typed income directly to deterministic calculators.")
    else:
        a(f"- self-employed files: **{ho['self_employed_delegated']}/{ho['self_employed_files']}** "
          f"delegated to `self_employed_specialist_agent`")
        a(f"- W-2 files wrongly delegated: **{ho['w2_wrongly_delegated']}/{ho['w2_files']}**")
        a("")
        a("Both directions are scored. An agent that always delegates would score 100% on the "
          "first line and is not making a decision.")
        if ho["misses"]:
            a("")
            for x in ho["misses"]:
                a(f"- ❌ `{x['case']}` — expected delegation `{x['expected_delegation']}`, got `{x['got']}`")
    a("")

    a("## 4. Tool-call precision and recall")
    a("")
    a("| | |")
    a("|---|---|")
    a(f"| Precision | **{_pct(tc['precision'])}** — forbidden calls made: {tc['false_positives_forbidden_calls']} |")
    a(f"| Recall | **{_pct(tc['recall'])}** — expected calls missed: {tc['false_negatives_missed_calls']} |")
    a(f"| F1 | **{_pct(tc['f1'])}** |")
    a("")
    a("This is conditional tool calling, quantified. A W-2 file must never reach "
      "`calc_self_employed_income`, and a file that is not yet an application must never "
      "reach the credit bureau. Note the menu is filtered from typed state before the agent "
      "sees it, so precision is partly a property of the architecture rather than of the "
      "model's restraint — which is the point.")
    if tc["problems"]:
        a("")
        for x in tc["problems"]:
            bits = []
            if x["missed_expected"]:
                bits.append(f"missed {', '.join(x['missed_expected'])}")
            if x["called_forbidden"]:
                bits.append(f"**called forbidden** {', '.join(x['called_forbidden'])}")
            a(f"- `{x['case']}` — {'; '.join(bits)}")
    a("")

    a("## 5. Run-to-run variance")
    a("")
    var = m["variance"]
    a("| Case | Runs | Modal decision | Agreement | Distinct outcomes | Median s |")
    a("|---|---|---|---|---|---|")
    for c in var["cases"]:
        a(f"| `{c['case']}` | {c['runs']} | {c['modal_decision']} | **{c['agreement']}** | "
          f"{len(c['distinct_decisions'])} | {c['seconds_median']} |")
    a("")
    a(f"**How to read this.** {var['interpretation']}")
    a("")

    a("## Cost and latency per agent")
    a("")
    cl = m["cost_latency"]
    a("| Agent | Invocations | Total s | Mean s | Total $ | Tools called |")
    a("|---|---|---|---|---|---|")
    for row in cl["per_agent"]:
        tools = ", ".join(f"{k}×{v}" for k, v in row["tools"].items()) or "—"
        a(f"| `{row['agent']}` | {row['invocations']} | {row['total_seconds']} | "
          f"{row['mean_seconds']} | ${row['total_usd']} | {tools} |")
    a("")
    a(f"Across the whole golden set: **{cl['run_total_seconds']}s**, **${cl['run_total_usd']}**.")
    if result["judgment"] == "stub" or result["mode"] == "replay":
        a("")
        a("> Cost reads $0 because no model was called — either the stub judgment ran, or the "
          "crews ran off cassettes. The metering is real; run `--judgment crew --mode live` "
          "for real figures.")
    a("")

    a("## Retrieval")
    a("")
    rt = m["retrieval"]
    a(f"- guideline queries asked per file: **{rt['mean_queries_per_file']}** (derived from typed "
      f"state — each question exists because something in the file makes it necessary)")
    a(f"- mean search/critique rounds **per query**: **{rt['mean_rounds_per_query']}**")
    a(f"- most rounds any single query needed: **{rt['max_rounds_on_any_query']}** "
      f"(cap is {rt['cap']})")
    a(f"- files where every query reached sufficiency: **{rt['files_all_queries_sufficient']}**")
    a("")
    a("A mean at or near 1.0 means the first reformulation usually retrieves enough. That is the "
      "critique loop reporting that it is *not* often needed, which is the honest reading — an "
      "always-requerying loop would look busier and be worse.")
    a("")

    a("## QC findings raised")
    a("")
    counts: Counter = Counter()
    for r in result["runs"]:
        for f in r.get("qc_findings", []) or []:
            counts[(f["severity"], f["kind"])] += 1
    if counts:
        a("| Severity | Kind | Count |")
        a("|---|---|---|")
        for (sev, kind), n in sorted(counts.items(), key=lambda kv: kv[0]):
            a(f"| {sev} | `{kind}` | {n} |")
    else:
        a("None.")
    a("")

    errs = [r for r in result["runs"] if r.get("error")]
    if errs:
        a("## Runs that crashed")
        a("")
        for r in errs:
            a(f"- `{r['case_id']}` — {r['error']}")
        a("")

    a("---")
    a("")
    a("### What this report does not tell you")
    a("")
    a("- **Narrative quality.** Nothing here scores whether a rationale reads well or is "
      "persuasive to an underwriter. That needs an LLM-as-judge and is on the roadmap.")
    a("- **Robustness to adversarial input.** One prompt-injection probe is exercised in the "
      "demo (`run_cli.py --policy-attack`) and it is not a suite.")
    a("- **Whether the guideline corpora are correct.** They are hand-written paraphrases "
      "written for this build. Citation validity measures internal consistency against the "
      "corpus, not fidelity to the real Selling Guide.")
    a("- **Anything about real loans.** The vendor responses are seeded fixtures.")
    a("")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    return "\n".join(L)


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _variance_headline(var: dict) -> str:
    if not var["cases"]:
        return "—"
    rates = [c["agreement_rate"] for c in var["cases"]]
    return f"{min(rates) * 100:.0f}–{max(rates) * 100:.0f}%"


# -- main ----------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="MERIDIAN eval harness")
    ap.add_argument("--judgment", choices=["stub", "crew"], default=None)
    ap.add_argument("--mode", choices=["auto", "replay", "record", "live"], default="auto")
    ap.add_argument("--roster", choices=["production", "demo"], default="production")
    ap.add_argument("--variance-runs", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="also write evals/report.json")
    ap.add_argument("--verbose-crewai", action="store_true")
    args = ap.parse_args()
    quiet_crewai(not args.verbose_crewai)

    spec = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # Defaults to `stub` **even when a key is present**, and that is deliberate. The
    # harness runs the golden set plus the variance sweep — 10 files plus 3 cases × N runs.
    # Under `crew` that is ~25 full agent runs, which on a live provider is hours of
    # wall-clock and real money, triggered by someone typing `run_evals.py` to see what it
    # does. Opting into that has to be explicit.
    judgment_kind = args.judgment or "stub"
    mode = resolve_mode(args.mode)
    # `or` treated an explicit `--variance-runs 0` as "not supplied" and quietly used the
    # fixture's 5. An out-of-range value is a CLI error, not something to reinterpret.
    if args.variance_runs is None:
        variance_runs = spec.get("variance_runs", 5)
    elif args.variance_runs < 1:
        ap.error("--variance-runs must be at least 1")
    else:
        variance_runs = args.variance_runs

    if judgment_kind == "crew" and mode == "live":
        total = len(spec["cases"]) + variance_runs * len(spec.get("variance_cases", []))
        print(
            f"NOTE: this will make about {total} full agent runs against "
            f"{', '.join(configured_models())}. Cost and wall-clock scale with that."
        )

    work_dir = ROOT / ".eval_work"
    work_dir.mkdir(exist_ok=True)

    cases = build_cases(spec)
    print(f"running {len(cases)} golden-set files  judgment={judgment_kind}  llm={mode}")

    runs = []
    for case in cases:
        r = run_case(case, judgment_kind, mode, work_dir, roster=args.roster)
        status = "ERROR" if r.get("error") else (r.get("decision") or "—")
        exp = case.get("expected", {}).get("decision")
        mark = "✅" if exp and status == exp else ("❌" if exp else " ")
        print(f"  {mark} {case['_case_id']:<44} {status}")
        runs.append(r)

    print(f"variance: {variance_runs} runs × {len(spec.get('variance_cases', []))} cases")
    variance = metric_variance(
        spec, cases, judgment_kind, mode, work_dir, variance_runs, roster=args.roster
    )

    from meridian.rag.store import get_store

    result = {
        "judgment": judgment_kind,
        "mode": mode,
        "roster": args.roster if judgment_kind == "crew" else "stub",
        "embedder": get_store().embedder.name,
        "n_cases": len(cases),
        "variance_runs": variance_runs,
        "runs": runs,
        "metrics": {
            "decision_accuracy": metric_decision_accuracy(runs),
            "citation_validity": metric_citation_validity(
                runs, semantic=judgment_kind == "crew"
            ),
            "handoff_correctness": metric_handoff_correctness(runs),
            "tool_calls": metric_tool_calls(runs),
            "variance": variance,
            "cost_latency": metric_cost_latency(runs),
            "retrieval": metric_retrieval(runs),
        },
    }

    write_report(result)
    if args.json:
        (Path(__file__).parent / "report.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )

    m = result["metrics"]
    print()
    print(f"  decision accuracy   {_pct(m['decision_accuracy']['rate'])}")
    print(f"  citation validity   {_pct(m['citation_validity']['rate'])}"
          f"   (hallucination {_pct(m['citation_validity']['hallucination_rate'])})")
    if judgment_kind == "crew" and args.roster == "demo":
        print(f"  handoff correctness {_pct(m['handoff_correctness']['rate'])}")
    else:
        print("  handoff correctness not scored (no agent handoff in this roster)")
    print(f"  tool precision      {_pct(m['tool_calls']['precision'])}"
          f"   recall {_pct(m['tool_calls']['recall'])}")
    print(f"  run-to-run          {_variance_headline(m['variance'])}")
    print()
    print(f"  → {REPORT}")

    failed = m["decision_accuracy"]["misses"] or [r for r in runs if r.get("error")]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
