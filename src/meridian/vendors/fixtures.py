"""Seeded vendor responses, keyed by loan.

Every vendor in this package is a mock, and the mock data lives with the scenario
rather than inside the vendor module. That keeps a scenario a single readable file — a
reviewer can open `data/scenarios/03_low_appraisal_deny.json` and see exactly why the
loan denies — and it means adding a golden-set variant for the eval harness is a
matter of copying a JSON file, not editing vendor code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _default_scenario_dir() -> Path:
    """Resolved from this file, not from the working directory.

    `Path("data/scenarios")` only worked when the process started at the repository root.
    Launched from anywhere else the registry came up empty and the failure surfaced later
    as "no scenario seeded for <loan_id>", which points at the wrong thing entirely.
    `MERIDIAN_SCENARIOS` overrides it for a deployment that ships them elsewhere.
    """
    override = os.environ.get("MERIDIAN_SCENARIOS")
    if override:
        return Path(override)
    # src/meridian/vendors/fixtures.py → vendors → meridian → src → <root>
    return Path(__file__).resolve().parents[3] / "data" / "scenarios"


SCENARIO_DIR = _default_scenario_dir()

_REGISTRY: dict[str, dict[str, Any]] = {}
_LOADED_ROOT: Path | None = None


def scenario_files(root: Path | None = None) -> list[Path]:
    d = Path(root) if root is not None else SCENARIO_DIR
    return sorted(d.glob("*.json")) if d.exists() else []


def load_scenarios(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load every scenario file into the registry, keyed by loan_id.

    Switching roots **replaces** the registry rather than merging into it. Merging meant a
    custom fixture root could return a stale scenario from the default root under the same
    id — a test that thought it was running its own fixture and was not.
    """
    global _LOADED_ROOT
    resolved = Path(root) if root is not None else SCENARIO_DIR
    if _LOADED_ROOT is not None and resolved != _LOADED_ROOT:
        _REGISTRY.clear()
    _LOADED_ROOT = resolved
    for p in scenario_files(resolved):
        data = json.loads(p.read_text(encoding="utf-8"))
        data["_source"] = str(p)
        _REGISTRY[data["loan_id"]] = data
    return _REGISTRY


def register(scenario: dict[str, Any]) -> None:
    """Used by the eval harness to inject a golden-set variant without a file."""
    _REGISTRY[scenario["loan_id"]] = scenario


def get_scenario(loan_id: str) -> dict[str, Any] | None:
    if loan_id not in _REGISTRY:
        load_scenarios()
    return _REGISTRY.get(loan_id)


def by_number(n: int, root: Path | None = None) -> dict[str, Any] | None:
    load_scenarios(root)
    for s in _REGISTRY.values():
        if s.get("scenario_id") == n:
            return s
    return None


def all_scenarios(root: Path | None = None) -> list[dict[str, Any]]:
    load_scenarios(root)
    return sorted(_REGISTRY.values(), key=lambda s: s.get("scenario_id", 99))


def fixture_for(loan_id: str, key: str) -> dict[str, Any] | None:
    s = get_scenario(loan_id)
    if not s:
        return None
    return s.get("vendor_fixtures", {}).get(key)


def clear() -> None:
    global _LOADED_ROOT
    _REGISTRY.clear()
    _LOADED_ROOT = None
