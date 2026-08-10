"""Shared crew plumbing: YAML config loading, tool adaptation, citation guardrails.

Agents and tasks live in `config/agents.yaml` and `config/tasks.yaml` per crew, mirroring
the multi-crew package layout of the reference Flow project on disk. Keeping the prompts
out of the Python means a reviewer can read what the agents are actually told without
reading around orchestration code.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Callable, Tuple

import yaml
from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool, tool

from ..core.config import model_for_tier, supports_structured_output
from ..tools import IRREVERSIBLE
from .guardrails import citations_must_resolve  # noqa: F401  (re-exported)


def load_yaml(crew_dir: Path, filename: str) -> dict[str, Any]:
    path = crew_dir / "config" / filename
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def as_tool(name: str, description: str, fn: Callable) -> BaseTool:
    """Adapt a gateway function into a CrewAI tool.

    The gateway wrapper stays underneath, so error classification and ledger accounting
    apply to a call made by an agent exactly as they do to one made by the Flow. There is
    one tool gateway, not two.
    """

    if name in IRREVERSIBLE:
        raise ValueError(
            f"{name!r} is irreversible and Flow-owned; it cannot be added to an agent menu"
        )

    @functools.wraps(fn)
    def _call(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    # CrewAI derives a tool's description from its docstring and refuses a function
    # without one. The gateway functions carry implementation notes rather than
    # agent-facing instructions, so the description written for the agent is installed
    # here deliberately — what the agent is told and what the maintainer reads are two
    # different audiences.
    _call.__doc__ = description
    _call.__name__ = name
    return tool(name)(_call)


def build_agent(spec: dict[str, Any], llm: Any, tools: list[BaseTool] | None = None) -> Agent:
    return Agent(
        role=spec["role"],
        goal=spec["goal"],
        backstory=spec["backstory"],
        llm=_llm_for(spec, llm),
        tools=tools or [],
        allow_delegation=bool(spec.get("allow_delegation", False)),
        max_iter=int(spec.get("max_iter", 8)),
        verbose=bool(spec.get("verbose", False)),
        reasoning=False,
    )


def _llm_for(spec: dict[str, Any], default: Any) -> Any:
    """Give an agent the model its `model_tier` asks for.

    Declared in `agents.yaml` next to the role and the tools, because which model an agent
    needs is a property of the job it does. An agent with no `model_tier` uses the run's
    default, so this is additive: nothing breaks if the field is absent.
    """
    tier = spec.get("model_tier")
    if not tier:
        return default
    wanted = model_for_tier(tier)
    if wanted == getattr(default, "model", None):
        return default

    # Ask the LLM to clone itself rather than reconstructing its class here. A test double
    # or any wrapper with a different __init__ signature simply declines and keeps serving,
    # which is what you want — an agent's model tier is a performance choice, not something
    # that should be able to break a substitute LLM.
    clone = getattr(default, "with_model", None)
    if not callable(clone):
        return default

    # Keyed by id(default) for speed, but the entry keeps a reference to the default it was
    # built from and that reference is verified on read. Both halves matter: holding the
    # reference stops CPython recycling the id onto a different object while an entry is
    # live, and the identity check means a stale entry can never be served even if it did.
    # Without either, a second run whose LLM landed on a recycled id would silently inherit
    # the previous run's clone — and with it the previous run's mode and cassette store.
    key = (wanted, id(default))
    entry = _LLM_CACHE.get(key)
    if entry is not None and entry[0] is default:
        return entry[1]
    cached = clone(wanted)
    _LLM_CACHE[key] = (default, cached)
    return cached


def reset_llm_cache() -> None:
    """Drop cached tier clones. Called between runs so nothing outlives its default."""
    _LLM_CACHE.clear()


_LLM_CACHE: dict[tuple, tuple[Any, Any]] = {}


def build_task(
    spec: dict[str, Any],
    agent: Agent,
    output_model: type | None = None,
    guardrail: Callable | None = None,
    context: list[Task] | None = None,
) -> Task:
    expected = spec["expected_output"]
    if output_model is not None and not supports_structured_output():
        expected = f"{expected}\n\n{_schema_instruction(output_model)}"
    return Task(
        name=spec.get("name"),
        description=spec["description"],
        expected_output=expected,
        agent=agent,
        output_pydantic=output_model,
        guardrail=guardrail,
        context=context or [],
    )


def _schema_instruction(model: type) -> str:
    """Describe the required JSON in the task's `expected_output`.

    Stated **once**, in the task definition, rather than appended to every LLM call. That
    distinction is the whole fix: CrewAI passes `response_model` on intermediate ReAct
    turns as well as the final one, so a per-call injection tells the agent to emit
    `Action:` and pure JSON at the same time and it deadlocks.

    Here it sits alongside the rest of the task's output contract, which is where an
    instruction about the shape of the answer belongs, and it only describes the *final*
    answer.
    """
    try:
        schema = json.dumps(model.model_json_schema(), indent=2)
    except Exception:
        return ""
    return (
        "Your Final Answer must be a single JSON object and nothing else — no prose around "
        "it and no markdown fence. It must validate against this JSON Schema, with every "
        "required property present and the exact property names shown:\n\n"
        f"{schema}"
    )


def sequential_crew(agents: list[Agent], tasks: list[Task]) -> Crew:
    """Sequential, and said out loud: crews run their tasks one at a time in one
    process. Claiming concurrency this system does not have would be the easiest thing
    in the walkthrough to get caught on."""
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=False)


def typed(result: Any, model: type, agent: str = "") -> Any:
    """Get the typed output off a crew result, or fail with something diagnosable.

    `result.pydantic` is the happy path, but it can legitimately be `None` — a guardrail
    that passes the `TaskOutput` through, or a model that wrapped its JSON in a sentence of
    prose, both land here. Rather than let an `AttributeError: 'NoneType' has no attribute
    'findings'` surface three frames away, parse the raw output and say what happened.

    This is not defensive padding. An agent returning almost-valid JSON is one of the most
    common real failure modes in a multi-agent system, and the useful behaviour is to
    recover where the content is actually there and to raise a legible error where it is
    not.
    """
    if getattr(result, "pydantic", None) is not None:
        return result.pydantic
    raw = getattr(result, "raw", None) or str(result)
    text = str(raw).strip()
    # Strip a markdown fence if the model added one.
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        import json as _json

        try:
            return model.model_validate(_json.loads(text[start : end + 1]))
        except Exception as exc:
            raise ValueError(
                f"{agent or model.__name__}: output could not be parsed as "
                f"{model.__name__}: {exc}\nraw output was:\n{text[:600]}"
            ) from exc
    raise ValueError(
        f"{agent or model.__name__}: output contained no JSON object to parse as "
        f"{model.__name__}.\nraw output was:\n{text[:600]}"
    )
