"""Governance crew — one agent, and the one that gets remembered.

A hallucinated citation is a *compliance* event at a lender, not a quality issue, and this
agent is the control that catches it. It is also the only agent whose output is partly
deterministic: the citation existence-and-support check is a Python function it calls as a
tool, so the verdict on any individual citation does not depend on the reviewer's mood.
The LLM's contribution is reading the rationale and conditions for prohibited-basis
language and judging whether stated adverse-action reasons are specific enough to act on —
which is genuine judgment, and not something a keyword list settles.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import tools as gw
from .._base import as_tool, build_agent, build_task, load_yaml, sequential_crew, typed
from ..schemas import EntailmentOutput, QCOutput

HERE = Path(__file__).parent

VERIFY_TOOL_DESC = (
    "Verify a guideline citation. Pass corpus ('fannie', 'fha' or 'overlays'), section (the "
    "identifier, e.g. 'OV-DTI-02') and claim (what the guideline is asserted to say — NOT the "
    "file's own figures). Returns exists, supports_claim, verified, the term overlap, the "
    "quoted section text and a note. Call this once per citation."
)


class GovernanceCrew:
    def __init__(self, llm: Any):
        self.agents_cfg = load_yaml(HERE, "agents.yaml")
        self.tasks_cfg = load_yaml(HERE, "tasks.yaml")
        self.llm = llm

    def build_qc(self):
        agent = build_agent(
            self.agents_cfg["compliance_qc_agent"],
            self.llm,
            tools=[as_tool("verify_citation", VERIFY_TOOL_DESC, gw.t_verify_citation)],
        )
        task = build_task(self.tasks_cfg["qc_review"], agent, output_model=QCOutput)
        return sequential_crew([agent], [task])

    def build_entailment(self):
        agent = build_agent(self.agents_cfg["compliance_qc_agent"], self.llm, tools=[])
        task = build_task(
            self.tasks_cfg["judge_citation_entailment"],
            agent,
            output_model=EntailmentOutput,
        )
        return sequential_crew([agent], [task])

    def run_entailment(self, section_text: str, claim: str) -> EntailmentOutput:
        result = self.build_entailment().kickoff(
            inputs={"section_text": section_text, "claim": claim}
        )
        return typed(result, EntailmentOutput, "compliance_qc_agent")

    def run_qc(self, context: dict[str, Any]) -> QCOutput:
        result = self.build_qc().kickoff(
            inputs={
                k: (json.dumps(v, indent=2, default=str) if isinstance(v, (dict, list)) else v)
                for k, v in context.items()
            }
        )
        return typed(result, QCOutput, "compliance_qc_agent")
