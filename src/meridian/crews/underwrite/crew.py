"""Underwrite crew — two agents, and the citation guardrail lives here.

`research_guidelines` carries `guardrail=citations_must_resolve`. If the researcher
returns output whose cited sections do not exist, the task is handed back with the reason
and retried. That is the first of three layers on hallucinated citations: the guardrail
here, the QC agent's support check downstream, and the measured rate in `evals/`.

A standalone `conditions_agent` was considered and merged into the underwriter. Conditions
are not a separate judgment — they *fall out of* the decision, and the same reasoning that
grants an overlay exception is the reasoning that writes the condition recording it.
Splitting them would have produced an agent whose only input was another agent's output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import tools as gw
from .._base import (
    as_tool,
    build_agent,
    build_task,
    citations_must_resolve,
    load_yaml,
    sequential_crew,
    typed,
)
from ..schemas import ResearchOutput, UnderwriteOutput

HERE = Path(__file__).parent

RESEARCH_TOOLS = {
    "search_guidelines": (
        gw.t_search_guidelines,
        "Search the guideline corpora. Pass question (str), corpora (list from 'fannie', "
        "'fha', 'overlays') and k (int). ALWAYS include 'overlays' — overlays govern where "
        "they conflict with agency guidance. Returns chunks with corpus, section, title, text "
        "and a similarity score. Cite only sections this tool actually returns to you.",
    ),
    "critique_retrieval": (
        gw.t_critique_retrieval,
        "Assess whether retrieved chunks are sufficient to answer a question. Pass question "
        "(str) and hits (the list returned by search_guidelines). Returns sufficient (bool), "
        "term coverage, whether an overlay chunk was retrieved, and the uncovered terms. If "
        "sufficient is false, reformulate the question and search again.",
    ),
}


class UnderwriteCrew:
    def __init__(self, llm: Any):
        self.agents_cfg = load_yaml(HERE, "agents.yaml")
        self.tasks_cfg = load_yaml(HERE, "tasks.yaml")
        self.llm = llm

    def build_research(self):
        agent = build_agent(
            self.agents_cfg["guideline_research_agent"],
            self.llm,
            tools=[as_tool(n, d, fn) for n, (fn, d) in RESEARCH_TOOLS.items()],
        )
        task = build_task(
            self.tasks_cfg["research_guidelines"],
            agent,
            output_model=ResearchOutput,
            guardrail=citations_must_resolve,
        )
        return sequential_crew([agent], [task])

    def run_research(self, questions: list[str], program: str) -> ResearchOutput:
        result = self.build_research().kickoff(
            inputs={
                "questions": "\n".join(f"  {i + 1}. {q}" for i, q in enumerate(questions)),
                "program": program,
            }
        )
        return typed(result, ResearchOutput, "guideline_research_agent")

    def build_underwrite(self):
        agent = build_agent(self.agents_cfg["underwriter_agent"], self.llm, tools=[])
        task = build_task(
            self.tasks_cfg["underwrite_file"],
            agent,
            output_model=UnderwriteOutput,
            guardrail=citations_must_resolve,
        )
        return sequential_crew([agent], [task])

    def run_underwrite(self, context: dict[str, Any]) -> UnderwriteOutput:
        result = self.build_underwrite().kickoff(
            inputs={k: (json.dumps(v, indent=2, default=str) if isinstance(v, (dict, list)) else v)
                    for k, v in context.items()}
        )
        return typed(result, UnderwriteOutput, "underwriter_agent")
