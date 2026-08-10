"""Deterministic retrieval over agency, FHA, and internal-overlay corpora.

Post-AUS the overlay layer is the whole job: DU says Approve/Eligible and the overlay
may still say no. Reconciling that is the underwriter's real task, which makes this a
better showcase for retrieval than answering guideline questions in the abstract. The
eight-agent demo roster retains a historical agent-driven caller; production routes,
reformulates, critiques, and requeries deterministically.
"""

from .store import Chunk, GuidelineStore, HashingEmbedder, get_store
from .tools import (
    critique_retrieval,
    parse_citations,
    reformulate,
    research_loop,
    route,
    search_guidelines,
    verify_citation,
)

__all__ = [
    "Chunk",
    "GuidelineStore",
    "HashingEmbedder",
    "critique_retrieval",
    "get_store",
    "parse_citations",
    "reformulate",
    "research_loop",
    "route",
    "search_guidelines",
    "verify_citation",
]
