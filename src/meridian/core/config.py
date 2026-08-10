"""Environment and model configuration, loaded in exactly one place.

`.env` is read here and only here. Every entry point (`run_cli.py`, `meridian.api.app`,
`evals/run_evals.py`, the tests) imports `meridian.core`, which imports this, so a key
sitting in `.env` is picked up without each script remembering to call `load_dotenv()` —
which is precisely the kind of thing that works in the CLI and then mysteriously doesn't
in the API process.

## Providers

Any OpenAI-compatible provider works, because CrewAI routes through LiteLLM. The provider
is inferred from the `MODEL` prefix:

    MODEL=deepseek/deepseek-v4-pro      + DEEPSEEK_API_KEY
    MODEL=gpt-4o-mini                   + OPENAI_API_KEY
    MODEL=anthropic/claude-sonnet-4-5   + ANTHROPIC_API_KEY

## The embeddings caveat, which is easy to get wrong

**DeepSeek does not serve an embeddings endpoint.** Only chat completions. So a DeepSeek
key gets you the eight agents but *not* `text-embedding-3-small`, and retrieval falls back
to the deterministic hashing embedder — which is lexical rather than semantic and genuinely
weaker at paraphrase.

That combination is worth naming rather than discovering: the agents are live, the
retrieval is not. `pick_embedder()` keys off `OPENAI_API_KEY` specifically for this reason,
and the eval report records which embedder ran so retrieval numbers are never compared
across the two.
"""

from __future__ import annotations

import os
from pathlib import Path

_LOADED = False


def load_env() -> None:
    """Read `.env` from the project root. Idempotent, and never overrides a real
    environment variable — an explicit export should win over a file."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        env = candidate / ".env"
        if env.exists():
            load_dotenv(env, override=False)
            return


load_env()


# Chat-completion key by provider prefix. The value is the env var LiteLLM expects.
PROVIDER_KEYS = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    # Azure is listed in STRUCTURED_OUTPUT_PROVIDERS below, so omitting it here made the
    # two tables disagree: an `azure/...` model was trusted for structured output and
    # simultaneously reported as having no credential, which silently forced replay.
    "azure": "AZURE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

DEFAULT_MODEL = "gpt-4o-mini"


def model_name() -> str:
    """The configured model, in LiteLLM's `provider/model` form where applicable."""
    configured = (os.environ.get("MERIDIAN_MODEL") or os.environ.get("MODEL") or "").strip()
    if configured:
        return configured
    if os.environ.get("OPENAI_API_KEY"):
        return DEFAULT_MODEL
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek/deepseek-v4-flash"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic/claude-sonnet-4-5"
    return DEFAULT_MODEL


def model_for_tier(tier: str | None) -> str:
    """Resolve an agent's model from its declared tier.

    Two tiers, because the eight agents are genuinely not equally hard:

    * **fast** — intake, income, credit, collateral. These read a record, call a
      calculator, and report. A cheaper model gets the same answer: measured on scenario 1,
      flash and pro both returned a qualifying income of exactly 12,125.00, and flash did it
      in 13s against 22s.
    * **strong** — guideline research, underwriting, compliance QC. These do the
      reconciliation and the citation work, which is where a weaker model starts inventing
      section numbers. The one thing not worth saving money on is the agent whose failure
      mode is a compliance event.

    Configured by `MODEL_FAST` / `MODEL_STRONG`, both falling back to `MODEL`, so a
    single-model setup still works and nothing here is DeepSeek-specific.
    """
    base = model_name()
    if tier == "strong":
        return (os.environ.get("MODEL_STRONG") or base).strip()
    if tier == "fast":
        return (os.environ.get("MODEL_FAST") or base).strip()
    return base


def provider(model: str | None = None) -> str:
    m = model or model_name()
    return m.split("/", 1)[0].lower() if "/" in m else "openai"


def chat_key_present(model: str | None = None) -> bool:
    """Is there a usable key for a model's provider?

    With no argument this checks **every model the run can actually reach** — the default
    plus whatever `MODEL_FAST` and `MODEL_STRONG` resolve to. Checking only the default was
    wrong once tiers existed: `MODEL=deepseek/...` with `MODEL_STRONG=gpt-4o` and no OpenAI
    key reported "key present", chose live mode, and then failed partway through a run at
    the first strong-tier agent — after the credit pull had already happened.

    A run either has credentials for all the models it will use, or it should not start.
    """
    load_env()
    if model is not None:
        var = PROVIDER_KEYS.get(provider(model))
        return bool(var and os.environ.get(var))
    return all(_has_key(m) for m in configured_models())


def configured_models() -> list[str]:
    """Every distinct model this run can select, default and both tiers."""
    seen: list[str] = []
    for tier in (None, "fast", "strong"):
        m = model_for_tier(tier)
        if m and m not in seen:
            seen.append(m)
    return seen


def missing_credentials() -> list[tuple[str, str]]:
    """(model, env var) for each configured model with no key. Empty when good to go."""
    load_env()
    return [
        (m, PROVIDER_KEYS.get(provider(m)) or f"<no known env var for provider {provider(m)!r}>")
        for m in configured_models()
        if not _has_key(m)
    ]


def _has_key(model: str) -> bool:
    var = PROVIDER_KEYS.get(provider(model))
    return bool(var and os.environ.get(var))


def embeddings_available() -> bool:
    """Only OpenAI serves the embedding model this project would use.

    Deliberately *not* `chat_key_present()`. A DeepSeek key means live agents and
    hash-based retrieval, and conflating the two would silently mislabel the embedder in
    the eval report.
    """
    load_env()
    return bool(os.environ.get("OPENAI_API_KEY"))


# USD per 1M tokens, (input, output). A model absent from this table reports token counts
# but no cost, rather than an invented one — see `pricing_known()`.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}


# Providers whose API accepts a full JSON-*schema* `response_format` (OpenAI's
# structured outputs). Others may still accept `{"type": "json_object"}` JSON mode, or
# nothing at all.
#
# DeepSeek is the reason this exists. It is OpenAI-*compatible*, so CrewAI routes it to
# the native OpenAI provider, which calls `beta.chat.completions.parse` with a json_schema
# response_format — and DeepSeek returns
# `400 This response_format type is unavailable now`.
# "OpenAI-compatible" is a claim about the endpoint shape, not about every feature behind
# it, and this is where that distinction bites.
STRUCTURED_OUTPUT_PROVIDERS = {"openai", "azure"}


def supports_structured_output(model: str | None = None) -> bool:
    return provider(model) in STRUCTURED_OUTPUT_PROVIDERS


def pricing_known(model: str) -> bool:
    return _bare(model) in PRICING


def price_per_million(model: str) -> tuple[float, float]:
    return PRICING.get(_bare(model), (0.0, 0.0))


def _bare(model: str) -> str:
    return model.split("/", 1)[-1]
