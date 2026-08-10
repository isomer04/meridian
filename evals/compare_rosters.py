#!/usr/bin/env python
"""Run stub, production, and demo over the same golden set and publish the comparison."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from run_evals import (
    GOLDEN,
    build_cases,
    metric_cost_latency,
    metric_decision_accuracy,
    run_case,
)

OUT = Path(__file__).with_name("roster-comparison.md")


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare all three MERIDIAN rosters")
    ap.add_argument("--mode", choices=["replay", "record", "live"], default="live")
    args = ap.parse_args()

    spec = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = build_cases(spec)
    configurations = [
        ("stub", "stub"),
        ("production", "crew"),
        ("demo", "crew"),
    ]
    results: dict[str, list[dict]] = {}
    with tempfile.TemporaryDirectory(prefix="meridian-rosters-") as temp:
        work = Path(temp)
        for roster, judgment in configurations:
            results[roster] = [
                run_case(
                    case,
                    judgment,
                    args.mode,
                    work,
                    tag=f"-{roster}",
                    roster=roster if judgment == "crew" else "production",
                )
                for case in cases
            ]

    lines = [
        "# Roster comparison",
        "",
        f"*Mode: **{args.mode}**. Human gates: **AUTO-APPROVED**.*",
        "",
        "| Roster | Decision accuracy | Errors | Wall seconds | Model cost |",
        "|---|---:|---:|---:|---:|",
    ]
    for roster, _ in configurations:
        runs = results[roster]
        accuracy = metric_decision_accuracy(runs)
        cost = metric_cost_latency(runs)
        errors = sum(1 for r in runs if r.get("error"))
        rate = "—" if accuracy["rate"] is None else f"{accuracy['rate'] * 100:.1f}%"
        lines.append(
            f"| {roster} | {rate} | {errors} | {cost['run_total_seconds']} | "
            f"${cost['run_total_usd']} |"
        )

    lines += [
        "",
        "## Case-by-case decisions",
        "",
        "| Case | Expected | Stub | Production | Demo | Referee |",
        "|---|---|---|---|---|---|",
    ]
    by_roster = {
        roster: {r["case_id"]: r for r in runs} for roster, runs in results.items()
    }
    for case in cases:
        case_id = case["_case_id"]
        expected = case.get("expected", {}).get("decision", "—")
        decisions = {
            roster: by_roster[roster][case_id].get("decision")
            or f"ERROR: {by_roster[roster][case_id].get('error')}"
            for roster, _ in configurations
        }
        agree = len(set(decisions.values())) == 1
        referee = "agree" if agree else f"golden label: {expected}"
        lines.append(
            f"| `{case_id}` | {expected} | {decisions['stub']} | {decisions['production']} | "
            f"{decisions['demo']} | {referee} |"
        )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
