"""The LLM, treated as what it is: a vendor.

It has a latency, a cost, a failure mode and a cassette, exactly like the credit bureau
and the AMC. That framing is deliberate — a vendor you cannot record, cannot price and
cannot fail gracefully is a vendor you cannot run in production.

`MeteredLLM` wraps CrewAI's own LLM and adds three things:

* **record / replay** through `CassetteStore`, sequence-aware so tool calls stay real;
* **cost and latency per agent per task**, so "which agent is expensive?" has an answer;
* **strict replay** — a cassette miss is a loud error, never a silent stub. A silently
  stubbed agent would make every eval number meaningless, which is worse than a crash.
"""

from __future__ import annotations

import json
import time
from typing import Any

from crewai import LLM
from crewai.llms.base_llm import BaseLLM
from pydantic import PrivateAttr

from ..core.events import emit
from ..core.replay import CassetteMiss, CassetteStore

from ..core.config import (
    model_name,
    price_per_million,
    pricing_known,
    supports_structured_output,
)

# Pricing lives in core/config.py. The default model comes from MODEL in .env, so the
# provider is whatever is configured rather than hardcoded here.
DEFAULT_MODEL = model_name()


class UsageMeter:
    """Accumulates per-(agent, task) usage for the run."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.unpriced_models: set[str] = set()

    def add(
        self,
        agent: str,
        task: str,
        model: str,
        seconds: float,
        prompt_tokens: int,
        completion_tokens: int,
        replayed: bool,
    ) -> None:
        pin, pout = price_per_million(model)
        usd = (prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout
        if not pricing_known(model):
            # No rate on file for this model. Report tokens and flag the gap rather than
            # printing $0.00, which reads as "free" instead of "unpriced".
            self.unpriced_models.add(model)
        self.rows.append(
            {
                "agent": agent,
                "task": task,
                "model": model,
                "seconds": round(seconds, 4),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "usd": round(usd, 6),
                "replayed": replayed,
            }
        )

    def for_agent(self, agent: str) -> dict[str, Any]:
        rows = [r for r in self.rows if r["agent"] == agent]
        return {
            "seconds": round(sum(r["seconds"] for r in rows), 4),
            "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["completion_tokens"] for r in rows),
            "usd": round(sum(r["usd"] for r in rows), 6),
            "replayed": all(r["replayed"] for r in rows) if rows else False,
        }

    def reset(self) -> None:
        self.rows.clear()
        self.unpriced_models.clear()


METER = UsageMeter()


class MeteredLLM(BaseLLM):
    """A cassette in front of the model and a meter behind it.

    **Composition, not inheritance, and for a concrete reason.** `crewai.LLM` defines a
    `__new__` that routes to a provider class — construct `LLM(model="gpt-4o-mini")` and
    you get back an `OpenAICompletion`. Subclassing `LLM` therefore produces an object
    that is not your subclass, and your `call` override is silently never invoked. That
    is a genuinely nasty failure mode: everything constructs, nothing errors, and the
    cassette layer simply does not exist. So this wraps a delegate instead.

    A side benefit worth having: in replay mode no provider is constructed at all, so a
    fresh clone with no API key runs the full crew path off cassettes without needing a
    key to exist.
    """

    model: str = DEFAULT_MODEL
    mode: str = "replay"

    _cassettes: CassetteStore | None = PrivateAttr(default=None)
    _delegate: Any = PrivateAttr(default=None)

    def __init__(
        self,
        model: str | None = None,
        mode: str = "replay",
        cassettes: CassetteStore | None = None,
        **kwargs: Any,
    ):
        super().__init__(model=model or model_name(), mode=mode, **kwargs)
        self._cassettes = cassettes
        self._delegate = None

    @property
    def cassettes(self) -> CassetteStore | None:
        return self._cassettes

    def with_model(self, model: str) -> "MeteredLLM":
        """A sibling on a different model, sharing this one's mode and cassette store.

        Used for per-agent model tiers. The cassette store is shared deliberately: cassettes
        are keyed by (scenario, agent, task), so two models writing into the same store do
        not collide, and a recording stays a recording of the whole run rather than one file
        per model.
        """
        return MeteredLLM(model=model, mode=self.mode, cassettes=self._cassettes)

    @property
    def delegate(self) -> Any:
        """Built lazily so replay never touches a provider."""
        if self._delegate is None:
            self._delegate = LLM(model=self.model)
        return self._delegate

    # Capability probes. CrewAI asks the LLM what it can do in several places — the
    # converter that repairs malformed structured output calls
    # `supports_function_calling()`, for instance — and a wrapper that omits one fails deep
    # inside the framework with an `AttributeError` that names the wrapper rather than the
    # missing method's purpose. Each of these forwards to the real provider, and answers
    # for itself in replay where there is no provider to ask.

    def supports_stop_words(self) -> bool:
        return True if self.mode == "replay" else self.delegate.supports_stop_words()

    def supports_function_calling(self) -> bool:
        """Forwarded to the provider — and it is why some cassettes come out incomplete.

        There is a genuine tension here, and it is worth stating rather than hiding.

        With native function calling on, the agents work well: a live DeepSeek run reached
        the right decision with 7/7 citations verified. But a tool-calling turn's response
        is a provider-specific *object*, not text, and cassettes record text. Recording
        `str(ChatCompletionMessageFunctionToolCall(...))` produces a Python repr that
        replays as gibberish.

        Forcing the text ReAct protocol instead (returning False here) makes every turn
        recordable, but it also makes the model noticeably worse at emitting the strict JSON
        the typed task outputs require — tried, and it broke `CollateralOutput` validation.

        So: live quality is preferred, and the recorder marks any cassette containing a
        non-text turn as incomplete rather than writing something that would replay wrongly.
        `--mode replay` then refuses that cassette with a clear message instead of producing
        a confident, meaningless run. See `CassetteStore.mark_unrecordable`.

        The honest summary is that record/replay currently covers text-protocol turns only.
        Closing that gap properly means serialising tool calls in a provider-neutral shape,
        which is a real piece of work and is on the roadmap — not a line of config.
        """
        if self.mode == "replay":
            return False
        try:
            return bool(self.delegate.supports_function_calling())
        except AttributeError:
            return False

    def get_context_window_size(self) -> int:
        return 128_000 if self.mode == "replay" else self.delegate.get_context_window_size()

    def reset_chain(self) -> None:
        self._delegate_call("reset_chain")

    def reset_reasoning_chain(self) -> None:
        self._delegate_call("reset_reasoning_chain")

    def _delegate_call(self, name: str) -> None:
        """Forward a no-return provider hook, tolerating its absence.

        Found by diffing this wrapper's public surface against the provider's rather than
        by waiting for the next `AttributeError` from inside the framework — the audit is
        cheap and the failure mode is expensive.
        """
        if self.mode == "replay":
            return
        fn = getattr(self.delegate, name, None)
        if callable(fn):
            fn()

    def call(self, messages: Any, **kwargs: Any) -> Any:
        agent = _agent_name(kwargs.get("from_agent"))
        task = _task_name(kwargs.get("from_task"))
        prompt = _flatten(messages)
        cs = self.cassettes

        if self.mode == "replay":
            if cs is None:
                raise CassetteMiss("replay mode requires a cassette store")
            cached = cs.get(agent, task, prompt)
            if cached is None:
                raise CassetteMiss(
                    f"no recorded turn for scenario={cs.scenario!r} agent={agent!r} "
                    f"task={task!r}. Record one with:  run_cli.py --judgment crew "
                    f"--mode record --scenario N   (needs credentials for the configured "
                    f"model provider). Strict replay "
                    f"fails loudly on purpose — a silently stubbed agent would make every "
                    f"eval number meaningless."
                )
            METER.add(agent, task, self.model, 0.0, 0, 0, replayed=True)
            emit("agent.done", actor=agent, task=task, source="cassette")
            return cached

        messages, kwargs = self._adapt_structured_output(messages, kwargs)

        # `get_token_usage_summary()` is *cumulative* over the delegate's lifetime, and one
        # delegate serves every turn of every task. Metering the summary directly charged
        # each call for the whole run so far, so token counts — and the dollar figure built
        # on them — grew quadratically with the number of turns. The delta is the call.
        before_pt, before_ct = _usage(self.delegate)
        t0 = time.perf_counter()
        result = self.delegate.call(messages, **kwargs)
        elapsed = time.perf_counter() - t0

        after_pt, after_ct = _usage(self.delegate)
        pt, ct = max(0, after_pt - before_pt), max(0, after_ct - before_ct)
        METER.add(agent, task, self.model, elapsed, pt, ct, replayed=False)
        emit(
            "agent.done",
            actor=agent,
            task=task,
            source="live",
            seconds=round(elapsed, 2),
            tokens=pt + ct,
        )

        if self.mode == "record" and cs is not None:
            if isinstance(result, str):
                cs.put(agent, task, prompt, result, model=self.model,
                       prompt_tokens=pt, completion_tokens=ct)
            else:
                # A native tool-call turn. Recording its repr would replay as gibberish, so
                # the cassette is flagged unusable instead of quietly poisoned.
                cs.mark_unrecordable(agent, task, type(result).__name__)
        return result


    def _adapt_structured_output(self, messages: Any, kwargs: dict[str, Any]):
        """Drop `response_model` for providers that reject a json_schema response_format.

        CrewAI turns `response_model` into OpenAI structured outputs, and DeepSeek answers
        `400 This response_format type is unavailable now`. So it is removed here.

        **What this deliberately no longer does is inject anything into the messages.** An
        earlier version appended "respond with only a JSON object matching this schema" to
        every call, which broke the agent badly and instructively: CrewAI passes
        `response_model` on *intermediate* ReAct turns too, where the agent is required to
        emit `Thought / Action / Action Input`. The agent was told to use the Action format
        and to emit nothing but JSON, simultaneously, and it looped until `max_iter` and
        then echoed CrewAI's own scaffolding back as its answer.

        The schema belongs in the task definition, which is stated once, rather than in
        every turn of the conversation — see `crews/_base.build_task`. The typed contract
        survives either way because `typed()` parses and validates the final output.
        """
        if kwargs.get("response_model") is None or supports_structured_output(self.model):
            return messages, kwargs
        # Strip it and change nothing else. The schema is carried in the task's
        # `expected_output` instead — see `crews/_base.build_task`.
        return messages, {k: v for k, v in kwargs.items() if k != "response_model"}


def _flatten(messages: Any) -> str:
    if isinstance(messages, str):
        return messages
    parts = []
    for m in messages or []:
        if isinstance(m, dict):
            parts.append(str(m.get("content", "")))
        else:
            parts.append(str(getattr(m, "content", m)))
    return "\n".join(parts)


def _agent_name(agent: Any) -> str:
    if agent is None:
        return "unknown_agent"
    return str(getattr(agent, "role", None) or getattr(agent, "id", "unknown_agent")).strip()


def _task_name(task: Any) -> str:
    if task is None:
        return "unknown_task"
    name = getattr(task, "name", None)
    if name:
        return str(name)
    desc = str(getattr(task, "description", "task"))
    return desc.strip().split("\n")[0][:48]


def _usage(llm: Any) -> tuple[int, int]:
    """Best-effort token counts. Reported as 0 rather than estimated when the provider
    does not return usage — an invented token count would produce an invented cost."""
    try:
        summary = llm.get_token_usage_summary()
        return int(getattr(summary, "prompt_tokens", 0)), int(
            getattr(summary, "completion_tokens", 0)
        )
    except Exception:
        return 0, 0


def build_llm(mode: str, scenario: str, model: str | None = None) -> MeteredLLM:
    cassettes = CassetteStore(scenario, mode=mode) if mode in ("replay", "record") else None
    return MeteredLLM(model=model or model_name(), mode=mode, cassettes=cassettes)
