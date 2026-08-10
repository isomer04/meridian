"""Retrieval-layer regressions.

Small, but each one is a bug that would have been invisible from the outside: a store
that answers, and answers plausibly, over the wrong matrix or an incomplete corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meridian.rag.store import CORPORA, GuidelineStore, HashingEmbedder  # noqa: E402
from meridian.rag.tools import verify_citation  # noqa: E402


@pytest.fixture()
def corpus(tmp_path):
    """A miniature three-corpus tree with the same `## SECTION — Title` shape."""
    for name, filename in CORPORA.items():
        (tmp_path / filename).write_text(
            f"## {name.upper()}-01 — Maximum ratios\n"
            f"The {name} corpus caps the total debt-to-income ratio at 45 percent.\n\n"
            f"## {name.upper()}-02 — Reserves\n"
            f"Two months of reserves are required on a principal residence.\n",
            encoding="utf-8",
        )
    return tmp_path


def test_embedder_name_carries_its_dimension(corpus):
    """The embedder's `name` is the embedding cache's filename.

    Held at class level, a 256-dimension store loaded the 1024-dimension store's cached
    matrix — same file, wrong column count. It does not fail where the mistake is; it
    fails inside the matrix multiply in `search()`, if it fails at all.
    """
    assert HashingEmbedder(256).name != HashingEmbedder(1024).name

    small = GuidelineStore(corpus, embedder=HashingEmbedder(256))
    assert small._matrix.shape[1] == 256

    large = GuidelineStore(corpus, embedder=HashingEmbedder(1024))
    assert large._matrix.shape[1] == 1024

    # Both must still search, which is the thing a shared cache broke.
    assert small.search("maximum debt-to-income ratio", k=2)
    assert large.search("maximum debt-to-income ratio", k=2)


def test_a_missing_corpus_file_is_an_error_not_a_smaller_index(corpus):
    """Skipping a missing corpus silently is how retrieval answers an FHA question out of
    the agency guide, or never returns the overlay chunks that bind."""
    (corpus / CORPORA["fannie"]).unlink()

    with pytest.raises(RuntimeError, match="fannie"):
        GuidelineStore(corpus, embedder=HashingEmbedder(256))


def test_golden_citation_cases_expose_the_semantic_inversion_gap():
    spec = json.loads(
        (Path(__file__).resolve().parents[1] / "evals" / "golden_set.json").read_text(
            encoding="utf-8"
        )
    )
    cases = spec["citation_cases"]
    lexical = [verify_citation(c["corpus"], c["section"], c["claim"]) for c in cases]

    assert all(v["verified"] for v in lexical), "both cases intentionally pass the lexical floor"
    assert [c["entailed"] for c in cases] == [True, False]
