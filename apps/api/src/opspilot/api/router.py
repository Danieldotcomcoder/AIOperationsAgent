"""Route assembly.

Every business router carries an authorization dependency. ``health`` is the only
unauthenticated surface, and it reveals nothing beyond up/down. The dev token route is
registered only in development/test (ADR 0008).
"""

from __future__ import annotations

from fastapi import APIRouter

from opspilot.api.routes import actions, documents, health, me, metrics, orders, runs, system
from opspilot.core.config import get_settings
from opspilot.ops.api import ops_router

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(me.router, tags=["identity"])
api_router.include_router(system.router, tags=["identity"])
api_router.include_router(runs.router, prefix="/runs", tags=["runs"])
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(documents.router, prefix="/documents", tags=["knowledge"])
api_router.include_router(actions.router, prefix="/actions", tags=["approval"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

# The simulated ERP boundary. Mounted in the same process but behind its own prefix
# and its own service-token guard, so the call from the execute worker is real HTTP
# with real failure modes (ADR 0003).
api_router.include_router(ops_router, prefix="/ops", tags=["ops (simulated ERP)"])

if get_settings().app_env.is_local:
    from opspilot.api.routes import dev_auth

    api_router.include_router(dev_auth.router, prefix="/auth/dev", tags=["dev only"])
