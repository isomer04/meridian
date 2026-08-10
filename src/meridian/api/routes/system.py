"""System capability route."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meridian.api.dependencies import get_evaluation_coordinator, get_run_coordinator
from meridian.api.models import SystemResponse
from meridian.application.evaluation_coordinator import EvaluationCoordinator
from meridian.application.run_coordinator import RunCoordinator
from meridian.core.config import missing_credentials
from meridian.core.replay import api_key_present
from meridian.version import VERSION

router = APIRouter(tags=["system"])


def _crew_setup_action() -> str:
    """Name the credential variables required by the configured model tiers."""
    variables = list(dict.fromkeys(variable for _, variable in missing_credentials()))
    if not variables:
        return "Restart the API to refresh model-provider credentials."
    return f"Set {' and '.join(variables)} and restart the API."


@router.get("/system", response_model=SystemResponse)
def get_system(
    coordinator: RunCoordinator = Depends(get_run_coordinator),
    evaluations: EvaluationCoordinator = Depends(get_evaluation_coordinator),
) -> SystemResponse:
    has_api_key = api_key_present()
    return SystemResponse(
        version=VERSION,
        api_key_present=has_api_key,
        default_judgment="crew" if has_api_key else "stub",
        active_run_id=coordinator.active_run_id,
        active_evaluation_id=evaluations.active_evaluation_id,
        crew_available=has_api_key,
        crew_unavailable_reason=None if has_api_key else "No model API key is configured.",
        crew_setup_action=(
            None
            if has_api_key
            else _crew_setup_action()
        ),
    )
