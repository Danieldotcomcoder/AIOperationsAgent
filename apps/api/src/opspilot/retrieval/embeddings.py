"""Development, paid OpenAI, or free OpenRouter Liquid embeddings."""

import hashlib
import math
import re
import uuid
from decimal import Decimal

from opspilot.core.config import Settings
from opspilot.core.errors import ProviderUnavailable
from opspilot.services.budget import BudgetService

#: Liquid's context is 512 tokens; UTF-8 bytes conservatively bound tokens (ADR 0015).
LIQUID_MAX_INPUT_BYTES = 500


def max_input_bytes(settings: Settings) -> int | None:
    """Largest single input the configured model accepts, or ``None`` if unbounded here."""
    return LIQUID_MAX_INPUT_BYTES if settings.embedding_provider == "openrouter" else None


def embedding_identity(settings: Settings) -> str:
    if settings.embedding_provider == "openrouter":
        return settings.embedding_model + ":pad1536-v1"
    return "fake-hash-v1" if settings.embedding_provider == "fake" else settings.embedding_model


def embed_texts(
    texts: list[str],
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    deadline_s: float | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    if settings.embedding_provider == "fake":
        vectors = []
        for text in texts:
            vector = [0.0] * settings.embedding_dim
            for word in re.findall(r"\w+", text.casefold()) or ["empty"]:
                digest = hashlib.sha256(word.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % len(vector)] += 1.0
            norm = math.sqrt(sum(x * x for x in vector))
            vectors.append([x / norm for x in vector])
        return vectors

    if settings.embedding_provider == "openrouter":
        return _embed_liquid(texts, settings, organization_id, deadline_s=deadline_s)

    # Restrict to the priced model until more embedding price entries are reviewed.
    if settings.embedding_model != "text-embedding-3-small":
        raise ProviderUnavailable("Unsupported embedding model; use text-embedding-3-small")
    from openai import APIError, OpenAI

    # UTF-8 bytes conservatively bound tokens. Keep the reservation on uncertain failures.
    reservation = max(
        Decimal("0.000001"), Decimal(sum(len(t.encode()) for t in texts)) * Decimal("0.00000002")
    )
    guard = BudgetService(settings, reservation_usd=reservation, count_run=False)
    guard.reserve(organization_id, uuid.UUID(int=0))
    try:
        with OpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=0) as client:
            response = client.embeddings.create(
                model=settings.embedding_model,
                input=texts,
                dimensions=settings.embedding_dim,
                timeout=deadline_s or settings.provider_call_deadline_seconds,
            )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(texts))):
            raise ProviderUnavailable("Incomplete embedding response")
        vectors = [item.embedding for item in ordered]
        if any(
            len(v) != settings.embedding_dim or not all(math.isfinite(x) for x in v) or not any(v)
            for v in vectors
        ):
            raise ProviderUnavailable("Invalid embedding response")
        guard.reconcile(
            organization_id,
            uuid.UUID(int=0),
            Decimal(response.usage.total_tokens) * Decimal("0.00000002"),
        )
        return vectors
    except APIError as exc:
        raise ProviderUnavailable("Embedding provider unavailable") from exc


def _embed_liquid(
    texts: list[str],
    settings: Settings,
    organization_id: uuid.UUID,
    *,
    deadline_s: float | None,
) -> list[list[float]]:
    from openai import APIError, OpenAI

    # The model's context is 512 tokens. The existing byte-based chunker uses
    # <=500 UTF-8 bytes, conservatively leaving room for special tokens. Reject
    # oversized queries rather than silently truncating meaning or averaging vectors.
    if any(
        not text.strip() or len(text.encode("utf-8")) > LIQUID_MAX_INPUT_BYTES for text in texts
    ):
        raise ProviderUnavailable("Liquid embedding input must be 1-500 UTF-8 bytes")
    guard = BudgetService(settings, reservation_usd=Decimal("0"), count_run=False)
    guard.reserve(organization_id, uuid.UUID(int=0))
    try:
        with OpenAI(
            api_key=settings.openrouter_api_key.get_secret_value(),
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
        ) as client:
            response = client.embeddings.create(
                model=settings.embedding_model,
                input=texts,
                encoding_format="float",
                timeout=deadline_s or settings.provider_call_deadline_seconds,
                extra_body={
                    "provider": {
                        "require_parameters": True,
                        "max_price": {"prompt": 0, "completion": 0},
                    }
                },
            )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(texts))):
            raise ProviderUnavailable("Incomplete embedding response")
        vectors = [item.embedding for item in ordered]
        if any(
            len(v) != 1024 or not all(math.isfinite(x) for x in v) or not any(v) for v in vectors
        ):
            raise ProviderUnavailable("Invalid Liquid embedding response")
        guard.reconcile(organization_id, uuid.UUID(int=0), Decimal("0"))
        # Zero padding preserves dot products and norms, hence cosine distances.
        # Keep the existing vector(1536) column and isolate by versioned identity.
        return [v + [0.0] * 512 for v in vectors]
    except APIError as exc:
        raise ProviderUnavailable("OpenRouter embedding provider unavailable") from exc
