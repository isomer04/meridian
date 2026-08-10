"""Deterministic retrieval with routing, critique, and bounded requery.

Production keeps the full loop reproducible. The historical demo roster may expose the search
and critique functions to its research agent, but production does not delegate these choices:

**Routing** is *deterministic*. Product type selects the corpus set, and the overlay
corpus is **always** included because overlays win (OV-GEN-01). Letting a model decide
whether to consult the binding policy layer would be a design error, not a feature.

**Reformulation** is *deterministic*. "Can this borrower qualify" is not a guideline
question; "what is the maximum DTI for a self-employed borrower and is there an
exception path" is. A closed rule table rewrites it.

**Retrieval critique** is *deterministic*. `critique_retrieval` asks whether the returned chunks
actually answer the question, and the loop requeries if they don't — capped at 3
rounds, because an uncapped critique loop is how you turn a 90-second demo into a
five-minute one.

**Citations are enforced** at three levels: the task guardrail rejects unparseable
output, `compliance_qc_agent` verifies the section exists *and* supports the claim,
and the eval harness reports the rate. A hallucinated citation is a compliance event
at a lender, so it gets three layers rather than a hopeful prompt.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..core.events import emit
from .store import GuidelineStore, get_store

MAX_ROUNDS = 3


def route(product: str, occupancy: str = "", employment: str = "") -> list[str]:
    """Deterministic corpus routing. Overlays are never optional."""
    product = (product or "").lower()
    if "fha" in product:
        return ["fha", "overlays"]
    return ["fannie", "overlays"]


def search_guidelines(
    question: str, corpora: list[str] | None = None, k: int = 4, store: GuidelineStore | None = None
) -> list[dict[str, Any]]:
    s = store or get_store()
    hits = s.search(question, corpora=corpora, k=k)
    emit(
        "tool.result",
        actor="rag.search_guidelines",
        query=question[:60],
        corpora=",".join(corpora or ["*"]),
        hits=len(hits),
        top=hits[0]["cite"] if hits else "none",
    )
    return hits


# Question templates for the deterministic reformulator used offline and in stub mode.
# The LLM path produces better rewrites; these exist so the loop is exercisable without
# a key, and so the *shape* of a good reformulation is visible in code.
REFORMULATIONS: list[tuple[str, str]] = [
    (r"self.?employ", "maximum debt-to-income ratio for a self-employed borrower and the documented exception path"),
    (r"\bdti\b|debt.to.income", "maximum total debt-to-income ratio and any exception path with compensating factors"),
    (r"second home", "maximum loan-to-value ratio for a second home and whether an exception is available"),
    (r"\bltv\b|loan.to.value|apprais(al|ed) value", "how loan-to-value is calculated on a purchase when the appraised value is below the contract price, and the maximum permitted"),
    (r"value acceptance|waiver", "when an offer of value acceptance may and may not be exercised"),
    (r"reserve", "minimum reserve requirement in months of the qualifying housing expense"),
    (r"credit score", "minimum representative credit score and how it is determined from a tri-merge"),
    (r"condo", "condominium project review requirements and warrantability"),
    (r"gap|employment history", "employment history documentation requirements including gaps"),
    (r"bonus|overtime|variable", "how bonus and overtime income are averaged and treated when declining"),
]


def reformulate(question: str) -> str:
    """Rewrite a loan-officer question as a guideline question.

    Offline this is a lookup table. With a model in the loop the agent does it, and
    does it better. Either way the *point* is that the raw question is not the query.
    """
    q = question.lower()
    for pattern, rewritten in REFORMULATIONS:
        if re.search(pattern, q):
            return rewritten
    return question


def critique_retrieval(question: str, hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Is this retrieval sufficient to answer the question?

    The offline heuristic is deliberately conservative and mechanical: sufficiency
    requires an overlay chunk (because overlays bind) and a reasonable term overlap
    between the question and the retrieved text. It will sometimes say "insufficient"
    when a human would say otherwise — which costs a requery, not a wrong answer.
    """
    if not hits:
        return {"sufficient": False, "reason": "no chunks returned", "missing": "anything"}

    terms = {t for t in re.findall(r"[a-z]{4,}", question.lower())}
    body = " ".join(h["text"].lower() + " " + h["title"].lower() for h in hits)
    covered = {t for t in terms if t in body}
    coverage = len(covered) / max(1, len(terms))
    has_overlay = any(h["corpus"] == "overlays" for h in hits)
    top_score = hits[0]["score"]

    sufficient = coverage >= 0.4 and has_overlay and top_score > 0.05
    reason_bits = []
    if coverage < 0.4:
        reason_bits.append(f"term coverage {coverage:.0%} below 40%")
    if not has_overlay:
        reason_bits.append("no overlay chunk retrieved — overlays govern, so this cannot be sufficient")
    if top_score <= 0.05:
        reason_bits.append(f"top similarity {top_score} is weak")

    result = {
        "sufficient": sufficient,
        "coverage": round(coverage, 2),
        "has_overlay_chunk": has_overlay,
        "top_score": top_score,
        "reason": "; ".join(reason_bits) or "chunks address the question and include the binding overlay layer",
        "uncovered_terms": sorted(terms - covered)[:8],
    }
    emit(
        "tool.result",
        actor="rag.critique_retrieval",
        sufficient=sufficient,
        coverage=result["coverage"],
    )
    return result


def research_loop(
    question: str,
    product: str = "conventional_conforming",
    k: int = 4,
    store: GuidelineStore | None = None,
    reformulator: Callable[[str], str] | None = None,
    max_rounds: int = MAX_ROUNDS,
    search_fn: Callable[..., list[dict[str, Any]]] | None = None,
    critique_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Route → search → critique → requery, capped.

    This is the loop the `guideline_research_agent` performs with tools when a model
    is available, and the exact same loop run deterministically when one is not. That
    correspondence is deliberate: the stub path is not a different algorithm, it is
    the same algorithm with the judgment steps replaced by rules, which is what makes
    the offline eval numbers meaningful rather than decorative.
    """
    corpora = route(product)
    reform = reformulator or reformulate
    # `search_fn` / `critique_fn` are injected by the caller so the loop can run through
    # the tool gateway — otherwise these calls bypass the ledger and tool-call precision
    # and recall would silently under-count the retrieval agent's work.
    if search_fn is not None:
        do_search = search_fn
    else:
        # The caller's store, not a fresh default one. Without this a test or a deployment
        # that built a store over a specific corpus had it ignored on the default path.
        def do_search(q: str, **kw: Any) -> list[dict[str, Any]]:
            return search_guidelines(q, store=store, **kw)

    do_critique = critique_fn or critique_retrieval
    rounds: list[dict[str, Any]] = []
    query = question
    hits: list[dict[str, Any]] = []

    # The three-round cap is a documented property of this loop, so it is enforced here
    # rather than trusted to every caller passing a sane `max_rounds`.
    rounds_allowed = max(1, min(int(max_rounds), MAX_ROUNDS))

    for i in range(rounds_allowed):
        if i > 0:
            query = reform(query)
        hits = do_search(query, corpora=corpora, k=k)
        critique = do_critique(question, hits)
        rounds.append(
            {"round": i + 1, "query": query, "cites": [h["cite"] for h in hits], "critique": critique}
        )
        if critique["sufficient"]:
            break
        if i == 0:
            # Widen before giving up: a missing overlay chunk is usually a k problem.
            k = min(k + 3, 8)

    return {
        "question": question,
        "corpora_routed": corpora,
        "rounds": rounds,
        "rounds_used": len(rounds),
        "sufficient": rounds[-1]["critique"]["sufficient"] if rounds else False,
        "chunks": hits,
        "citations": [h["cite"] for h in hits],
    }


# -- citation verification ----------------------------------------------

CITE_RE = re.compile(r"\b(fannie|fha|overlays)\s*[:\s]\s*([A-Z0-9][A-Za-z0-9\.\-]{2,})")


def parse_citations(text: str) -> list[tuple[str, str]]:
    """Pull `corpus:SECTION` references out of agent prose.

    Kept strict. A citation the parser cannot read is treated as absent, and the task
    guardrail rejects the output — which is better than a lenient parser silently
    accepting something that does not resolve.
    """
    out: list[tuple[str, str]] = []
    for m in CITE_RE.finditer(text):
        pair = (m.group(1).lower(), m.group(2).rstrip(".,;)"))
        if pair not in out:
            out.append(pair)
    return out


# Guideline prose and underwriter prose use different words for the same concept. A
# lexical support check that does not know this rejects a perfectly good citation because
# the overlay wrote "LTV" and the underwriter wrote "loan-to-value" — a false positive
# that would make the citation-validity metric worse than useless, because it would
# understate a rate people are going to act on. Encoded once here rather than by
# hand-tuning the corpus wording until the checker is happy.
SYNONYMS = [
    (r"\bltv\b", "loan-to-value"),
    (r"\bcltv\b", "combined loan-to-value"),
    (r"\bdti\b", "debt-to-income"),
    (r"\bpiti\b", "monthly housing expense"),
    (r"\bprimary residence\b", "principal residence"),
    (r"\bcredit score\b", "representative credit score"),
    (r"\bcaps\b", "maximum"),
    (r"\bcapped\b", "maximum"),
    (r"\bpct\b|\%", "percent"),
    (r"\bmos\b", "months"),
    (r"\bself.employment\b", "self-employed"),
]


def _normalize(text: str) -> str:
    out = text.lower()
    for pattern, replacement in SYNONYMS:
        out = re.sub(pattern, replacement, out)
    return out


def verify_citation(
    corpus: str, section: str, claim: str, store: GuidelineStore | None = None
) -> dict[str, Any]:
    """Two separate questions, because they fail separately.

    1. Does the section **exist**? A fabricated section number is the obvious failure.
    2. Does it **support the claim**? A real section cited for something it does not
       say is the subtler and more dangerous failure — it survives a spot check.

    Support is scored by term overlap between the claim and the section text, plus
    agreement on any numeric threshold the claim asserts. A number in the claim that
    does not appear in the section is treated as unsupported, which is the check that
    catches "the overlay caps DTI at 47%".

    **Known limitation, stated rather than hidden.** This function is the deterministic
    floor. It does not catch a logical inversion that reuses a section's vocabulary;
    crew QC therefore sends only citations that pass here to a downgrade-only entailment
    judge. Stub runs stop at this floor, and their report labels that lower ceiling.
    """
    s = store or get_store()
    chunk = s.get(corpus, section)
    if chunk is None:
        return {
            "corpus": corpus,
            "section": section,
            "claim": claim,
            "exists": False,
            "supports_claim": False,
            "verified": False,
            "qc_note": f"section {corpus}:{section} does not exist in the corpus",
        }

    text = _normalize(chunk.text + " " + chunk.title)
    claim_l = _normalize(claim)
    terms = {t for t in re.findall(r"[a-z]{4,}", claim_l)}
    overlap = len({t for t in terms if t in text}) / max(1, len(terms))

    # Section identifiers contain digits (OV-VA-01, B3-6-02) and those digits are not
    # thresholds. Left in, a claim like "no OV-VA-01 disqualifier is present" reads as
    # asserting the number 01 and fails against a section that never mentions it.
    claim_for_numbers = re.sub(r"\b(?:fannie|fha|overlays)?:?\s?[A-Z]{1,3}[\dA-Z]*(?:[.-][\dA-Z]+)+\b", " ", claim, flags=re.I)
    claim_numbers = set(re.findall(r"\d+(?:\.\d+)?", claim_for_numbers.lower()))
    text_numbers = set(re.findall(r"\d+(?:\.\d+)?", text))
    unmatched = {n for n in claim_numbers if n not in text_numbers}
    # Spelled-out thresholds are common in guideline prose ("45 percent" vs "forty-five").
    words = {
        "80": "eighty", "85": "eighty-five", "90": "ninety", "95": "ninety-five",
        "97": "ninety-seven", "43": "forty-three", "45": "forty-five", "50": "fifty",
        "31": "thirty-one", "36": "thirty-six", "660": "660", "700": "700", "720": "720",
        "2": "two", "3": "three", "6": "six", "30": "thirty",
    }
    unmatched = {n for n in unmatched if words.get(n, n) not in text}

    supports = overlap >= 0.35 and not unmatched
    note_bits = []
    if overlap < 0.35:
        note_bits.append(f"term overlap {overlap:.0%} below 35%")
    if unmatched:
        note_bits.append(
            "claim asserts " + ", ".join(sorted(unmatched)) + " which does not appear in the cited section"
        )

    return {
        "corpus": corpus,
        "section": section,
        "claim": claim,
        "exists": True,
        "supports_claim": supports,
        "verified": supports,
        "overlap": round(overlap, 2),
        "quoted": chunk.text[:220],
        "section_text": chunk.text,
        "qc_note": "; ".join(note_bits) or "section exists and its text supports the claim",
    }
