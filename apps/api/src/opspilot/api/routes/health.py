"""Liveness and readiness — different questions, different answers.

``/health/live``  : is this process running? Never touches a dependency. If it checked
                    the database, a database blip would make the orchestrator kill
                    healthy containers.
``/health/ready`` : can this process serve traffic right now? Checks the dependencies
                    it actually needs, and is what Caddy and the deploy script gate on.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from redis import Redis, RedisError
from sqlalchemy import text

from opspilot.auth.dependencies import DbSession
from opspilot.core.config import get_settings
from opspilot.core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
def ready(session: DbSession, response: Response) -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("health.database_unavailable", error=type(exc).__name__)
        checks["database"] = "unavailable"
        session.rollback()

    try:
        with Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1
        ) as client:
            client.ping()
        checks["redis"] = "ok"
    except RedisError as exc:
        log.warning("health.redis_unavailable", error=type(exc).__name__)
        checks["redis"] = "unavailable"

    healthy = all(value == "ok" for value in checks.values())
    if not healthy:
        response.status_code = 503
    return {
        "status": "ready" if healthy else "not_ready",
        "checks": checks,
        # Useful during a rollback: confirms which build answered.
        "version": "0.1.0",
        "env": settings.app_env.value,
    }
