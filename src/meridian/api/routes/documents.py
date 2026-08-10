"""Allowlisted static-document routes.

Only two fixed slugs are ever served, each mapped to a path resolved at import
time from a project-root-relative literal. There is no user-controlled path
component anywhere in this module, so a `..`/absolute-path style traversal
attempt has no code path to reach the filesystem through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status

from meridian.api.models import DocumentResponse, ErrorEnvelope, ErrorResponse
from meridian.application.evaluation_service import PROJECT_ROOT

router = APIRouter(tags=["documents"])

DOCUMENTS: dict[str, tuple[str, Path]] = {
    "architecture": ("Architecture", PROJECT_ROOT / "docs" / "architecture.md"),
    "assumptions": ("Assumptions", PROJECT_ROOT / "docs" / "assumptions.md"),
}


@router.get(
    "/documents/{slug}",
    response_model=DocumentResponse,
    responses={404: {"model": ErrorEnvelope}},
)
def get_document(slug: Literal["architecture", "assumptions"]) -> DocumentResponse:
    title, path = DOCUMENTS[slug]
    try:
        markdown = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(code="document_not_found", message=f"{slug} has no source document yet").model_dump(),
        ) from None
    return DocumentResponse(slug=slug, title=title, markdown=markdown)
