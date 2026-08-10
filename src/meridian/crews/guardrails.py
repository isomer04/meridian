"""Task guardrails.

NOTE: this module deliberately does **not** use `from __future__ import annotations`.
CrewAI introspects a guardrail's return annotation and requires it to be literally
`Tuple[bool, Any]`. Under PEP 563 the annotation becomes the *string*
`"Tuple[bool, Any]"` and validation fails with a message that points at the type rather
than at the import — which cost real time to track down, hence this comment.
"""

from typing import Any, Tuple

from ..rag import parse_citations
from ..rag.store import get_store


def citations_must_resolve(output: Any) -> Tuple[bool, Any]:
    """Task-level guardrail: reject output whose citations do not resolve.

    This is the first of three layers. The guardrail rejects unparseable or
    non-existent references and hands the task back to the agent to retry;
    `compliance_qc_agent` then checks whether each real section actually *supports* the
    claim; and the eval harness reports the resulting rate. A hallucinated citation is a
    compliance event at a lender, so one hopeful instruction in a prompt is not enough.

    Note this rejects on **existence**, not on support. Support needs the claim as well
    as the section, which is the QC agent's job — a guardrail that tried to do both here
    would either be too weak to matter or too strict to converge.

    The return annotation is `Tuple[bool, Any]` from `typing`, not the builtin `tuple`.
    CrewAI introspects a guardrail's annotation and rejects it if it does not literally
    match, and `from __future__ import annotations` turns the builtin form into a string
    it will not accept.
    """
    store = get_store()
    refs = _structured_citations(output) or parse_citations(_output_text(output))
    if not refs:
        return (
            False,
            "No guideline citations found. Every finding must cite a section as "
            "corpus:SECTION — for example overlays:OV-DTI-02 or fannie:B3-6-02.",
        )
    missing = [f"{c}:{s}" for c, s in refs if not store.section_exists(c, s)]
    if missing:
        return (
            False,
            f"These cited sections do not exist in the corpus: {', '.join(missing)}. "
            "Cite only sections returned by the search tool. Do not invent a section "
            "identifier.",
        )
    return True, output


def _structured_citations(output: Any):
    """Pull (corpus, section) pairs out of a typed output's `citations` field.

    This is the fix for a hole that a live run walked straight through. The regex path
    below looks for inline `corpus:SECTION` text, but a task with `output_pydantic` returns
    citations as *separate JSON fields* — `{"corpus": "fannie", "section": "OV-VA-01"}` —
    which contain no colon and so matched nothing. The guardrail passed happily while the
    underwriter filed an overlay section under the agency corpus and invented
    `OV-VA-LENDER-01` outright; only the QC agent downstream caught it.

    A guardrail that silently inspects the wrong representation is worse than no guardrail,
    because it reports success. Structured citations are checked first now, and the text
    regex is the fallback for prose output.
    """
    pydantic_out = getattr(output, "pydantic", None)
    citations = getattr(pydantic_out, "citations", None) if pydantic_out is not None else None
    if not citations:
        return []
    refs = []
    for c in citations:
        corpus = getattr(c, "corpus", None)
        section = getattr(c, "section", None)
        if not corpus or not section:
            continue
        pair = (str(corpus).strip().lower(), str(section).strip())
        if pair not in refs:
            refs.append(pair)
    return refs


def _output_text(output: Any) -> str:
    for attr in ("raw", "pydantic", "json_dict"):
        value = getattr(output, attr, None)
        if value is None:
            continue
        if attr == "pydantic":
            return value.model_dump_json()
        return str(value)
    return str(output)
