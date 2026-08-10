"""Intake crew — one agent.

`document_classifier` was a separate agent in an earlier cut and was merged in here.
Classifying a document is an extraction step *inside* intake, not a role with its own
judgment and its own failure mode, and a pipeline stage does not earn an agent. That
merge is documented in docs/architecture.md as a deliberate scoping decision rather than an
omission.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .._base import build_agent, build_task, load_yaml, sequential_crew, typed
from ..schemas import IntakeOutput

HERE = Path(__file__).parent


class IntakeCrew:
    def __init__(self, llm: Any):
        self.agents_cfg = load_yaml(HERE, "agents.yaml")
        self.tasks_cfg = load_yaml(HERE, "tasks.yaml")
        self.llm = llm

    def build(self):
        agent = build_agent(self.agents_cfg["intake_agent"], self.llm, tools=[])
        task = build_task(
            self.tasks_cfg["detect_application_completeness"], agent, output_model=IntakeOutput
        )
        return sequential_crew([agent], [task])

    def run(self, borrower: dict, property_: dict, loan: dict, documents: list) -> IntakeOutput:
        # `property_`, because `property` is a builtin. The input *key* stays "property" —
        # that name is fixed by the `{property}` placeholder in tasks.yaml.
        result = self.build().kickoff(
            inputs={
                "borrower": json.dumps(borrower, indent=2, default=str),
                "property": json.dumps(property_, indent=2, default=str),
                "loan": json.dumps(loan, indent=2, default=str),
                "documents": json.dumps(
                    [{"filename": d.get("filename"), "type": d.get("type"), "extracted": d.get("extracted")} for d in documents],
                    indent=2,
                    default=str,
                ),
            }
        )
        return typed(result, IntakeOutput, "intake_agent")
