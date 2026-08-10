"""Scenario catalogue route."""

from fastapi import APIRouter

from meridian.application.dashboard import scenario_options
from meridian.application.models import ScenarioOption

router = APIRouter(tags=["scenarios"])


@router.get("/scenarios", response_model=list[ScenarioOption])
def get_scenarios() -> list[ScenarioOption]:
    return scenario_options()
