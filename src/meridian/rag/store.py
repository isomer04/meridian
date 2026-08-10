"""Retrieval over three guideline corpora. numpy cosine, no vector database.

Deliberately **not chromadb**. The reference project on disk carries the comment
"pin to avoid embedchain/langchain-openai conflicts" and had to drop CrewAI to
0.186.1 to resolve it. A readable amount of numpy is lower risk than a dependency
that constrains the framework version, and it is better walkthrough material.

Two embedders behind one interface:

* `OpenAIEmbedder` — text-embedding-3-small. Used when a key is present.
* `HashingEmbedder` — deterministic character-n-gram hashing into a fixed vector.
  Used offline. It is genuinely weaker at paraphrase than a learned embedding, and
  the honest way to describe it is "lexical, not semantic" — but it is deterministic,
  free, and it keeps the whole retrieval path exercisable on a fresh clone with no
  secrets. The eval harness records which embedder produced a run so retrieval
  numbers are never compared across the two.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from ..core.config import embeddings_available


def _default_guideline_dir() -> Path:
    """Locate the bundled corpora without depending on the caller's working directory.

    `Path("data/guidelines")` only resolved when the process happened to start at the
    repository root, so the dashboard API launched from anywhere else, or a test run from
    `tests/`, got "no guideline chunks found" — a packaging problem wearing a retrieval
    problem's error message. Resolved from this file instead, with
    `MERIDIAN_GUIDELINES` as the deployment override.
    """
    override = os.environ.get("MERIDIAN_GUIDELINES")
    if override:
        return Path(override)
    # src/meridian/rag/store.py → src/meridian/rag → src/meridian → src → <root>
    return Path(__file__).resolve().parents[3] / "data" / "guidelines"


GUIDELINE_DIR = _default_guideline_dir()

CORPORA = {
    "fannie": "fannie_selling_guide.md",
    "fha": "fha_4000_1.md",
    "overlays": "internal_overlays.md",
}

SECTION_RE = re.compile(r"^##\s+([A-Za-z0-9\.\-]+)\s+—\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    corpus: str
    section: str
    title: str
    text: str

    @property
    def cite(self) -> str:
        return f"{self.corpus}:{self.section}"

    def render(self) -> str:
        return f"[{self.cite}] {self.title}\n{self.text}"


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Character 4-gram hashing. Lexical, deterministic, free, offline."""

    name = "hashing-4gram-1024"

    def __init__(self, dim: int = 1024):
        self.dim = dim
        # Per-instance, because `name` is the embedding cache's filename. Two stores at
        # different dimensions shared one cache file under the class-level name, and the
        # second one loaded a matrix with the wrong column count — which fails, if it
        # fails at all, inside the `@` in `search()` rather than where the mistake is.
        self.name = f"hashing-4gram-{dim}"

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            s = re.sub(r"\s+", " ", t.lower())
            for tok in re.findall(r"[a-z0-9\.\-]+", s):
                # whole tokens carry most of the signal for guideline text
                out[i, int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 2.0
                for j in range(max(1, len(tok) - 3)):
                    g = tok[j : j + 4]
                    out[i, int(hashlib.md5(g.encode()).hexdigest(), 16) % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class OpenAIEmbedder:
    name = "text-embedding-3-small"

    def __init__(self, model: str = "text-embedding-3-small"):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model

    def encode(self, texts: list[str]) -> np.ndarray:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        arr = np.array([d.embedding for d in resp.data], dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.maximum(norms, 1e-9)


def pick_embedder() -> Embedder:
    # `embeddings_available()` rather than "is there any key": DeepSeek serves chat
    # completions but no embeddings endpoint, so a DeepSeek-only setup runs live agents
    # over hash-based retrieval, and mislabelling that would corrupt the eval report.

    if embeddings_available():
        try:
            return OpenAIEmbedder()
        except (ImportError, OSError, ValueError) as exc:
            # A bare `except: pass` here meant a key present but rejected, or the `openai`
            # package missing, silently downgraded retrieval to lexical hashing — and the
            # only trace was an embedder name in the eval report that nobody reads twice.
            # Narrowed to setup and configuration failures so anything genuinely unexpected
            # still propagates rather than being absorbed by the fallback.
            logging.getLogger(__name__).warning(
                "OpenAIEmbedder unavailable (%s: %s) — falling back to %s, which is "
                "lexical rather than semantic. Retrieval quality will differ.",
                type(exc).__name__,
                exc,
                HashingEmbedder.__name__,
            )
    return HashingEmbedder()


def parse_corpus(corpus: str, path: Path) -> list[Chunk]:
    """One chunk per `## SECTION — Title` heading. Section IDs are the citation
    surface, so they are parsed rather than generated."""
    text = path.read_text(encoding="utf-8")
    matches = list(SECTION_RE.finditer(text))
    chunks: list[Chunk] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        chunks.append(Chunk(corpus, m.group(1), m.group(2).strip(), body))
    return chunks


class GuidelineStore:
    def __init__(self, root: Path | None = None, embedder: Embedder | None = None):
        self.root = root or GUIDELINE_DIR
        self.embedder = embedder or pick_embedder()
        self.chunks: list[Chunk] = []
        missing = []
        for corpus, filename in CORPORA.items():
            p = self.root / filename
            if not p.exists():
                # Every corpus is required. Skipping a missing one silently is how you get
                # a store that answers FHA questions out of the agency guide, or — worse —
                # one that never returns the overlay chunks that bind.
                missing.append(f"{corpus} ({p})")
                continue
            self.chunks.extend(parse_corpus(corpus, p))
        if missing:
            raise RuntimeError(
                f"guideline corpus files missing under {self.root}: {', '.join(missing)}"
            )
        if not self.chunks:
            raise RuntimeError(f"no guideline chunks found under {self.root}")
        self._matrix = self._embed_chunks()
        self._index = {c.cite: c for c in self.chunks}

    def _embed_chunks(self) -> np.ndarray:
        cache = self.root / f".embcache_{self.embedder.name}.json"
        payload = [f"{c.section} {c.title}\n{c.text}" for c in self.chunks]
        fp = hashlib.sha256("|".join(payload).encode()).hexdigest()[:16]
        if cache.exists():
            try:
                blob = json.loads(cache.read_text())
                if blob.get("fingerprint") == fp:
                    return np.array(blob["matrix"], dtype=np.float32)
            except Exception:
                pass
        matrix = self.embedder.encode(payload)
        try:
            cache.write_text(json.dumps({"fingerprint": fp, "matrix": matrix.tolist()}))
        except Exception:
            pass
        return matrix

    def search(self, query: str, corpora: list[str] | None = None, k: int = 4) -> list[dict[str, Any]]:
        qv = self.embedder.encode([query])[0]
        scores = self._matrix @ qv
        allowed = set(corpora) if corpora else None
        ranked = sorted(
            (
                (float(scores[i]), c)
                for i, c in enumerate(self.chunks)
                if allowed is None or c.corpus in allowed
            ),
            key=lambda t: -t[0],
        )
        return [
            {
                "corpus": c.corpus,
                "section": c.section,
                "title": c.title,
                "text": c.text,
                "cite": c.cite,
                "score": round(s, 4),
            }
            for s, c in ranked[:k]
        ]

    # -- citation verification ------------------------------------------

    def section_exists(self, corpus: str, section: str) -> bool:
        return f"{corpus}:{section}" in self._index

    def get(self, corpus: str, section: str) -> Chunk | None:
        return self._index.get(f"{corpus}:{section}")

    def sections(self, corpus: str | None = None) -> list[str]:
        return [c.cite for c in self.chunks if corpus is None or c.corpus == corpus]


_STORES: dict[Path, GuidelineStore] = {}


def get_store(root: Path | None = None) -> GuidelineStore:
    """Cached per resolved root.

    A single global meant that whichever caller got there first decided the corpus for
    everyone: `get_store(tmp_path)` in a test, then `get_store()` in the code under test,
    and the second call quietly served the first one's fixture corpus.
    """
    key = Path(root) if root is not None else GUIDELINE_DIR
    store = _STORES.get(key)
    if store is None:
        store = GuidelineStore(key)
        _STORES[key] = store
    return store
