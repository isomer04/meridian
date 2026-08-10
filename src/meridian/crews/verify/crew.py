"""Verify crew — four agents, and the most interesting wiring in the system.

`income_analyst_agent` has `allow_delegation=True` and is the only agent that does. Its
tool menu is **filtered from typed state before it is constructed**, so on a W-2 file the
self-employed calculator is not merely discouraged, it is absent. The analyst's correct
move on a self-employed file is therefore to delegate to
`self_employed_specialist_agent`, which is reached only by that handoff and never by
routing.

That distinction is worth being precise about in a walkthrough: the *menu* is
deterministic, the *choice within it* is agentic, and the *handoff* is agent-initiated.
Three different mechanisms in one step, and the eval harness measures whether the handoff
actually happened rather than taking it on trust.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import tools as gw
from .._base import as_tool, build_agent, build_task, load_yaml, sequential_crew, typed
from ..schemas import CollateralOutput, CreditOutput, IncomeOutput

HERE = Path(__file__).parent

TOOL_DESCRIPTIONS = {
    "calc_w2_income": (
        "Compute monthly qualifying income for a salaried or hourly W-2 borrower. Pass the "
        "borrower record as a dict. Returns the qualifying figure, the method, the component "
        "breakdown and analyst notes. Copy the returned figure exactly — do not adjust it."
    ),
    "calc_self_employed_income": (
        "Perform a Schedule C / K-1 cash-flow analysis for a self-employed borrower. Pass the "
        "borrower record as a dict. Applies add-backs and deductions and handles a declining "
        "trend correctly. Copy the returned figure exactly."
    ),
    "verify_employment": (
        "Verify employment with the employer. Pass loan_id and employer. Returns status, start "
        "date and probability of continued employment."
    ),
    "verify_deposits": (
        "Verify asset accounts. Pass loan_id. Returns accounts, total verified assets and any "
        "large deposits requiring sourcing."
    ),
    "search_guidelines": (
        "Search the guideline corpora. Pass question (str), corpora (list of 'fannie', 'fha', "
        "'overlays') and k. Always include 'overlays'. Returns chunks with corpus, section, "
        "title, text and a similarity score. Cite only sections this tool returns."
    ),
    "calc_ltv": (
        "Compute LTV and CLTV. Pass the loan dict and optionally appraised_value. Uses the "
        "lesser of purchase price or appraised value on a purchase."
    ),
}


class VerifyCrew:
    def __init__(self, llm: Any):
        self.agents_cfg = load_yaml(HERE, "agents.yaml")
        self.tasks_cfg = load_yaml(HERE, "tasks.yaml")
        self.llm = llm

    # -- income, with the conditional menu and the handoff ---------------

    def _menu(self, names: dict[str, Any]) -> list:
        missing = [n for n in names if n not in TOOL_DESCRIPTIONS]
        if missing:
            # A bare KeyError here names the dict, not the tool. The description *is* the
            # agent-facing instruction, so a gateway tool added without one has to fail
            # where it is obvious rather than reaching an agent undescribed.
            raise KeyError(
                f"no agent-facing description for tool(s): {', '.join(sorted(missing))} — "
                f"add them to TOOL_DESCRIPTIONS in {__name__}"
            )
        return [as_tool(n, TOOL_DESCRIPTIONS[n], fn) for n, fn in names.items()]

    def build_income(self, income_type: str):
        # The menu is built from typed state. This is the deterministic half of
        # conditional tool calling; the agent's freedom is inside what it is handed.
        menu = gw.tools_for_income_analyst(income_type)
        analyst = build_agent(
            self.agents_cfg["income_analyst_agent"], self.llm, tools=self._menu(menu)
        )
        def specialist_calc(borrower: dict) -> dict:
            # CrewAI's delegation changes agents internally, outside our Flow methods.
            # Name the actor at the boundary that matters so the gateway ledger observes
            # the specialist's actual call rather than inheriting the analyst's actor.
            gw.set_actor("self_employed_specialist_agent")
            return gw.t_calc_self_employed_income(borrower)

        specialist = build_agent(
            self.agents_cfg["self_employed_specialist_agent"],
            self.llm,
            tools=self._menu({"calc_self_employed_income": specialist_calc}),
        )
        task = build_task(self.tasks_cfg["analyze_income"], analyst, output_model=IncomeOutput)
        return sequential_crew([analyst, specialist], [task])

    def run_income(self, income_type: str, borrower: dict) -> IncomeOutput:
        result = self.build_income(income_type).kickoff(
            inputs={
                "income_type": income_type,
                "borrower": json.dumps(borrower, indent=2, default=str),
            }
        )
        return typed(result, IncomeOutput, "income_analyst_agent")

    # -- credit ----------------------------------------------------------

    def build_credit(self):
        agent = build_agent(self.agents_cfg["credit_liability_agent"], self.llm, tools=[])
        task = build_task(self.tasks_cfg["interpret_credit"], agent, output_model=CreditOutput)
        return sequential_crew([agent], [task])

    def run_credit(self, credit: dict, dti: dict, ltv: dict, occupancy: str) -> CreditOutput:
        result = self.build_credit().kickoff(
            inputs={
                "credit": json.dumps(
                    {k: v for k, v in credit.items() if k != "interpretation"}, indent=2, default=str
                ),
                "dti": json.dumps(dti, indent=2, default=str),
                "ltv": json.dumps(ltv, indent=2, default=str),
                "occupancy": occupancy,
            }
        )
        return typed(result, CreditOutput, "credit_liability_agent")

    # -- collateral ------------------------------------------------------

    def build_collateral(self):
        # `property_`, because `property` is a builtin. The *input key* stays "property":
        # that name is fixed by the `{property}` placeholder in tasks.yaml.
        agent = build_agent(
            self.agents_cfg["collateral_agent"],
            self.llm,
            tools=self._menu(
                {"search_guidelines": gw.t_search_guidelines, "calc_ltv": gw.t_calc_ltv}
            ),
        )
        task = build_task(
            self.tasks_cfg["decide_collateral"], agent, output_model=CollateralOutput
        )
        return sequential_crew([agent], [task])

    def run_collateral(self, aus_findings: dict, property_: dict) -> CollateralOutput:
        result = self.build_collateral().kickoff(
            inputs={
                "aus_findings": json.dumps(aus_findings, indent=2, default=str),
                "property": json.dumps(property_, indent=2, default=str),
            }
        )
        return typed(result, CollateralOutput, "collateral_agent")
