"""Turn off CrewAI's own console panels.

CrewAI's `EventListener` constructs its `ConsoleFormatter` with `verbose=True`
hardcoded, so there is no setting to pass. Left on, it prints a boxed panel per flow
method and per crew step, which buries the trace this system emits from
`core/events.py` — and that trace is the thing worth watching in a walkthrough.

This reaches into a singleton, which is not something to do lightly, so it is quarantined
in one small module, it is reversible, and it fails silently if CrewAI's internals move.
`--verbose-crewai` turns the panels back on when you want to see what the framework itself
is doing.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_MUTED_LOGGERS = ("crewai", "LiteLLM", "litellm", "httpx", "opentelemetry")
_TELEMETRY_VARS = ("CREWAI_DISABLE_TELEMETRY", "OTEL_SDK_DISABLED")

# What the environment looked like before quiet mode touched it. `None` means "was not
# set", which is a different thing from "was set to something" and has to be restored as
# such — leaving `CREWAI_DISABLE_TELEMETRY=true` behind after `--verbose-crewai` would be
# this module silently outliving its own scope.
_SAVED: dict[str, Any] = {}


def quiet_crewai(quiet: bool = True) -> None:
    try:
        from crewai.events.event_listener import EventListener

        listener = EventListener()
        formatter = getattr(listener, "formatter", None)
        if formatter is not None:
            formatter.verbose = not quiet

            # Flow panels ("🌊 Flow Started", "✅ Flow Method Completed") do not consult
            # `verbose` at all — `print_panel` only suppresses them in TUI mode. So the
            # flow-level panels have to be intercepted rather than configured away.
            original = getattr(formatter, "_meridian_original_print_panel", None) or formatter.print_panel
            formatter._meridian_original_print_panel = original

            def print_panel(content, title, style="blue", is_flow=False, _orig=original):
                if quiet and is_flow:
                    return
                return _orig(content, title, style, is_flow)

            formatter.print_panel = print_panel
    except Exception:  # noqa: BLE001 - never fatal; CrewAI internals are allowed to move
        log.debug("could not patch the CrewAI console formatter", exc_info=True)

    if quiet:
        if not _SAVED:
            _SAVED["env"] = {v: os.environ.get(v) for v in _TELEMETRY_VARS}
            _SAVED["levels"] = {n: logging.getLogger(n).level for n in _MUTED_LOGGERS}
        for var in _TELEMETRY_VARS:
            # Assigned, not `setdefault`. An existing `OTEL_SDK_DISABLED=false` is exactly
            # the case quiet mode is being asked to override, and `setdefault` deferred to
            # it — so telemetry kept running with quiet mode reporting success.
            os.environ[var] = "true"
        for name in _MUTED_LOGGERS:
            logging.getLogger(name).setLevel(logging.ERROR)
    elif _SAVED:
        for var, value in _SAVED["env"].items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        for name, level in _SAVED["levels"].items():
            logging.getLogger(name).setLevel(level)
        _SAVED.clear()
