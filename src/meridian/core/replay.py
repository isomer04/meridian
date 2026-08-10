"""Record / replay for LLM calls — cassettes keyed by (scenario, agent, task).

Not optional, for three reasons:

1. On a live demo over a network API, the network is the highest-probability
   failure. Interview wifi, a corporate proxy, a rate limit, or a slow model turning
   a 90-second run into five minutes.
2. Build iteration is ~10x faster and free.
3. The eval harness can run the same file N times for the run-to-run variance
   metric at zero cost.

And it is itself a talking point: *"you're watching the orchestration, not the model
— I can flip to live if you'd like."*

Offline mode, precisely. With no API key the default judgment is `StubJudgment` — rules,
no model, no cassettes involved. Cassette-backed *agents* are a separate, explicit
choice: `--judgment crew --mode replay`. There is no path on which cassettes are served
automatically in place of a missing key, and describing one would misrepresent what a
fresh clone actually runs.

On the cassette path the run still exercises the real Flow, the real routers, the real
saga, and the real tools; only the model's tokens come from disk. A cassette miss in
strict replay is a loud error, never a silent stub — a silently stubbed agent would make
the eval numbers meaningless.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .events import emit

CASSETTE_DIR = Path(os.environ.get("MERIDIAN_CASSETTES", "data/cassettes"))


class CassetteMiss(RuntimeError):
    """Strict replay wanted a recorded response and there wasn't one."""


def _slug(text: str) -> str:
    """Readable and collision-resistant.

    The sanitise-and-truncate half keeps cassette paths legible, which matters because you
    read them while debugging. On its own it is not injective: two agent roles sharing a
    60-character prefix, or differing only in punctuation, collapse onto one file and
    silently share a recording. The digest is taken over the *whole* original string, so
    the name stays readable and distinct values stay distinct.
    """
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:60].strip("_") or "x"
    return f"{keep}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"


def fingerprint(prompt: str) -> str:
    """Content hash of the prompt, used as a secondary key.

    Cassettes are looked up by (scenario, agent, task) first because that survives
    prompt edits, which is what you want mid-build. The fingerprint is recorded
    alongside so you can tell *why* a lookup is stale.
    """
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


class CassetteStore:
    """Cassettes hold a **sequence** of responses per (scenario, agent, task), not one.

    This matters more than it looks. A CrewAI agent runs a ReAct-style loop: think →
    call a tool → observe → think again → answer. That is several LLM round trips for
    one task. A cassette holding a single response would have to be the final answer,
    which means the agent never calls a tool, which means tool-call precision and recall
    measured under replay would be measuring nothing.

    Recording the whole turn sequence and replaying it in order keeps the tool calls
    real: the model's tokens come from disk, and everything else — the tools, the saga,
    the gateway, the policy controls — actually executes. That is what makes
    *"you're watching the orchestration, not the model"* a true statement rather than a
    convenient one.
    """

    MODES = ("replay", "record", "live")

    def __init__(self, scenario: str, mode: str = "replay", root: Path | None = None):
        # Not an `assert`: `python -O` strips it, and the value comes from `resolve_mode`
        # / a CLI flag, so an unrecognised mode would then construct a store that silently
        # behaves as neither replay nor record.
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}, got {mode!r}")
        self.scenario = scenario
        self.mode = mode
        self.root = (root or CASSETTE_DIR) / _slug(scenario)
        if mode == "record":
            self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self._cursor: dict[str, int] = {}
        self._recorded: dict[str, list[dict[str, Any]]] = {}
        self._unrecordable: dict[str, set[str]] = {}
        self.corrupt = 0
        self.stale = 0

    def path(self, agent: str, task: str) -> Path:
        return self.root / f"{_slug(agent)}__{_slug(task)}.json"

    def _key(self, agent: str, task: str) -> str:
        return f"{agent}::{task}"

    def mark_unrecordable(self, agent: str, task: str, kind: str) -> None:
        """Flag a task whose turns cannot be faithfully recorded.

        A native tool-call turn is an object, not text. Writing its `repr` would produce a
        cassette that replays confidently and wrongly, which is the worst possible outcome
        for something whose entire purpose is fidelity. So the file is marked instead, and
        replay refuses it.
        """
        key = self._key(agent, task)
        self._unrecordable.setdefault(key, set()).add(kind)
        self._recorded.setdefault(key, [])
        self._write(key)

    def get(self, agent: str, task: str, prompt: str) -> str | None:
        """Next response in this task's recorded sequence, or None on a miss."""
        p = self.path(agent, task)
        if not p.exists():
            self.misses += 1
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A half-written cassette (Ctrl-C during a record run, before writes were made
            # atomic) is a miss, not a crash — and it is counted separately so `stats()`
            # can say "corrupt" rather than blaming the recording for being absent.
            self.corrupt += 1
            self.misses += 1
            return None
        if data.get("incomplete"):
            raise CassetteMiss(
                f"cassette for agent={agent!r} task={task!r} is marked incomplete: it "
                f"contains turn(s) of type {', '.join(data.get('unrecordable_turn_types', []))} "
                f"that cannot be recorded as text (native provider tool calls). Run this "
                f"scenario with --mode live, or see MeteredLLM.supports_function_calling."
            )
        turns = data.get("turns") or []
        key = self._key(agent, task)
        i = self._cursor.get(key, 0)
        if i >= len(turns):
            self.misses += 1
            return None

        # Lookup by (scenario, agent, task) surviving prompt edits is deliberate — that is
        # what makes replay usable mid-build. What is not acceptable is doing it *quietly*:
        # a turn recorded against a different prompt is an answer to a different question,
        # and a reviewer reading the trace has to be able to see that happened. So it is
        # counted and announced, and `stats()` reports it, rather than being a fingerprint
        # written to disk and never compared to anything.
        recorded_fp = turns[i].get("prompt_fingerprint")
        if recorded_fp and recorded_fp != fingerprint(prompt):
            self.stale += 1
            emit(
                "agent.done",
                actor=agent,
                task=task,
                source="cassette",
                stale=True,
                note="prompt has changed since this turn was recorded; re-record to refresh",
            )

        self._cursor[key] = i + 1
        self.hits += 1
        return turns[i].get("response")

    def put(self, agent: str, task: str, prompt: str, response: str, **meta: Any) -> None:
        """Append a turn and write the file immediately.

        Flushing on every turn rather than at the end of the run is deliberate. Recording
        is the expensive path — it is the one that costs money — and an exception anywhere
        downstream (a guardrail rejecting output, a crashed crew, Ctrl-C on a slow model)
        would otherwise discard everything already paid for. Writing through means a
        partial recording survives, and a partial recording is worth strictly more than
        none: the turns already on disk replay, and only the missing tail needs re-running.
        """
        key = self._key(agent, task)
        self._recorded.setdefault(key, []).append(
            {"prompt_fingerprint": fingerprint(prompt), "response": response, **meta}
        )
        self._write(key)

    def _write(self, key: str) -> None:
        agent, task = key.split("::", 1)
        turns = self._recorded.get(key) or []
        p = self.path(agent, task)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "scenario": self.scenario,
            "agent": agent,
            "task": task,
            "turns": turns,
        }
        bad = sorted(self._unrecordable.get(key, ()))
        if bad:
            payload["incomplete"] = True
            payload["unrecordable_turn_types"] = bad
        # Write-through happens on every turn, so an interrupt lands in the middle of one
        # often enough to matter. Temp file plus `os.replace` means a reader either sees
        # the previous complete cassette or the new one, never half of either.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def flush(self) -> list[Path]:
        """Rewrite every recorded task. `put` already writes through, so this is a
        belt-and-braces call for an explicit end-of-run checkpoint."""
        for key in list(self._recorded):
            self._write(key)
        return [self.path(*k.split("::", 1)) for k in self._recorded]

    def stats(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "corrupt": self.corrupt,
            "stale": self.stale,
            "incomplete_tasks": sorted(self._unrecordable),
        }


def api_key_present() -> bool:
    """Is there a key for the configured model's provider?

    Delegates to `core.config` so adding a provider is a one-line change there rather
    than a grep for every place a key was checked.
    """
    from .config import chat_key_present

    return chat_key_present()


def resolve_mode(requested: str | None) -> str:
    """`auto` means: live if a key exists, replay otherwise.

    Defaulting to replay when there is no key is what makes `run_cli.py` work on a
    fresh clone with no secrets, which is how a reviewer will first run it.
    """
    if requested and requested != "auto":
        return requested
    return "live" if api_key_present() else "replay"
