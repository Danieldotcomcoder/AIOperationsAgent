import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from opspilot.core.config import Settings
from opspilot.core.errors import ProviderUnavailable
from opspilot.retrieval import embeddings
from opspilot.services.budget import BudgetService


@pytest.mark.parametrize("bad_response", [False, True])
def test_hosted_embeddings_reserve_before_call_and_validate(monkeypatch, bad_response) -> None:
    import openai

    calls = []
    guard = MagicMock()
    guard.reserve.side_effect = lambda *args: calls.append("reserve")
    monkeypatch.setattr(embeddings, "BudgetService", lambda *args, **kwargs: guard)
    client = MagicMock()
    client.__enter__.return_value = client

    def create(**kwargs):
        calls.append("embed")
        assert kwargs["dimensions"] == 1536
        assert kwargs["timeout"] == 2
        vector = [1.0] if bad_response else [1.0] + [0.0] * 1535
        return SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=vector)], usage=SimpleNamespace(total_tokens=4)
        )

    client.embeddings.create.side_effect = create
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
    settings = Settings(_env_file=None, embedding_provider="openai", openai_api_key="test")
    if bad_response:
        with pytest.raises(ProviderUnavailable):
            embeddings.embed_texts(["policy"], settings, uuid.uuid4(), deadline_s=2)
        guard.reconcile.assert_not_called()
    else:
        result = embeddings.embed_texts(["policy"], settings, uuid.uuid4(), deadline_s=2)
        assert len(result[0]) == 1536
        guard.reconcile.assert_called_once()
    assert calls == ["reserve", "embed"]


def test_embedding_budget_does_not_consume_run_quota() -> None:
    from decimal import Decimal

    client = MagicMock()
    client.eval.return_value = 0
    guard = BudgetService(
        Settings(_env_file=None), client, reservation_usd=Decimal("0.0001"), count_run=False
    )
    receipt = guard.reserve(uuid.uuid4(), uuid.uuid4())
    assert receipt["amount"] == 100
    assert receipt["count"] == 0


@pytest.mark.parametrize("bad", [None, "dimension", "nan", "zero", "index"])
def test_liquid_transport_validates_and_pads(monkeypatch, bad):
    import openai

    guard = MagicMock()
    monkeypatch.setattr(embeddings, "BudgetService", lambda *a, **kw: guard)
    client = MagicMock()
    client.__enter__.return_value = client
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(openai, "OpenAI", constructor)
    vector = [1.0, 2.0] + [0.0] * 1022
    if bad == "dimension":
        vector = [1.0]
    elif bad == "nan":
        vector[0] = float("nan")
    elif bad == "zero":
        vector = [0.0] * 1024
    client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=1 if bad == "index" else 0, embedding=vector)]
    )
    settings = Settings(
        _env_file=None,
        embedding_provider="openrouter",
        embedding_model="liquid/lfm-2.5-embedding-350m:free",
        openrouter_api_key="test",
    )
    if bad:
        with pytest.raises(ProviderUnavailable):
            embeddings.embed_texts(["policy"], settings, uuid.uuid4(), deadline_s=2)
        guard.reconcile.assert_not_called()
    else:
        result = embeddings.embed_texts(["policy"], settings, uuid.uuid4(), deadline_s=2)
        assert result == [vector + [0.0] * 512]
        assert sum(v * v for v in result[0]) == sum(v * v for v in vector)
        guard.reconcile.assert_called_once()
    constructor.assert_called_once_with(
        api_key="test",
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
    )
    request = client.embeddings.create.call_args.kwargs
    assert "dimensions" not in request
    assert request["encoding_format"] == "float"
    assert request["timeout"] == 2
    assert request["extra_body"]["provider"]["max_price"] == {"prompt": 0, "completion": 0}
    assert embeddings.embedding_identity(settings).endswith(":pad1536-v1")


def test_liquid_rejects_oversized_utf8_before_network(monkeypatch):
    guard = MagicMock()
    monkeypatch.setattr(embeddings, "BudgetService", guard)
    settings = Settings(
        _env_file=None,
        embedding_provider="openrouter",
        embedding_model="liquid/lfm-2.5-embedding-350m:free",
        openrouter_api_key="test",
    )
    with pytest.raises(ProviderUnavailable, match="UTF-8"):
        embeddings.embed_texts(["é" * 251], settings, uuid.uuid4())
    guard.assert_not_called()


def test_policy_query_respects_liquid_input_without_truncating() -> None:
    from opspilot.core.errors import RequestInvalid
    from opspilot.services.tools import embedding_query

    limit = embeddings.LIQUID_MAX_INPUT_BYTES
    assert embedding_query("late carrier", "SO-1042 in transit", limit) == (
        "late carrier\nSO-1042 in transit",
        True,
    )
    # Both fields individually fit the tool schema, but not together: the optional
    # context is left out visibly instead of cutting either text.
    assert embedding_query("q" * 400, "c" * 400, limit) == ("q" * 400, False)
    # Multibyte text is measured in bytes, which is what the model limit is about.
    with pytest.raises(RequestInvalid):
        embedding_query("é" * 260, None, limit)
    # Providers without a per-input limit here keep the full context.
    assert embedding_query("q" * 400, "c" * 400, None) == ("q" * 400 + "\n" + "c" * 400, True)
    settings = Settings(
        _env_file=None,
        embedding_provider="openrouter",
        embedding_model="liquid/lfm-2.5-embedding-350m:free",
        openrouter_api_key="test",
    )
    assert embeddings.max_input_bytes(settings) == limit
    assert embeddings.max_input_bytes(Settings(_env_file=None)) is None
