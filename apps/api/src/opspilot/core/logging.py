"""Structured logging.

Rules, enforced by review because no linter can catch them:

* Log operational metadata, never content. No prompt bodies, no document text, no
  shipment notes, no tokens, no secrets. A digest and a length are usually enough.
* Every line carries ``request_id`` and, inside a run, ``run_id`` and
  ``organization_id``, bound through :mod:`opspilot.core.context`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog

from opspilot.core.config import Settings

# Keys that must never appear in a log event, whatever a caller passes.
_REDACTED_KEYS = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "id_token",
        "api_key",
        "password",
        "secret",
        "prompt",
        "messages",
        "raw_text",
        "document_text",
        "note",
        "request_text",
        "payload_json",
        "notes",
        "content",
        "text",
        "description",
        "correction_text",
        "refresh_token",
        "client_secret",
        "openai_api_key",
        "anthropic_api_key",
        "openrouter_api_key",
        "ops_api_service_token",
        "exception",
        "exc_info",
    }
)


def _rename_logger(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    """``logger`` is a reserved keyword in ``structlog.get_logger``, so the module name
    travels as ``logger_name`` and is renamed here to the conventional output key."""
    if "logger_name" in event:
        event["logger"] = event.pop("logger_name")
    return event


def _redact(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[redacted]" if str(key).lower() in _REDACTED_KEYS else clean(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        return value

    return cast(dict[str, Any], clean(event))


def configure_logging(settings: Settings) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        # The generic processors, not the stdlib ones: we render straight to stdout
        # via PrintLoggerFactory, and stdlib's add_logger_name expects a stdlib logger.
        # The module name is bound by get_logger() instead.
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _rename_logger,
        _redact,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level)
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "celery.app.trace"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a *lazy* proxy carrying the module name.

    Passing initial values to ``get_logger`` rather than calling ``.bind()`` matters:
    ``.bind()`` instantiates the bound logger immediately, so a module-level
    ``log = get_logger(__name__)`` would freeze whatever configuration existed at
    import time — which is before ``configure_logging`` runs in the lifespan hook.
    """
    return structlog.get_logger(logger_name=name)  # type: ignore[no-any-return]
