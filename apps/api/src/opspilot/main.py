"""Application factory.

Wiring only. No business logic lives here — if a decision is being made in this file,
it belongs in a service.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from opspilot.api.router import api_router
from opspilot.auth.verifier import build_verifier
from opspilot.core.config import get_settings
from opspilot.core.errors import OpsPilotError
from opspilot.core.logging import configure_logging, get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    # Built once: JWKS caching and key rotation handling depend on a single instance.
    app.state.verifier = build_verifier(settings)
    app.state.settings = settings
    log.info(
        "app.start",
        env=settings.app_env.value,
        auth_mode=settings.auth_mode,
        llm_provider=settings.llm_provider,
        writes_enabled=settings.feature_writes_enabled,
    )
    yield
    log.info("app.stop")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OpsPilot API",
        version="0.1.0",
        lifespan=lifespan,
        # No docs in production: the schema enumerates every route and parameter.
        docs_url="/docs" if settings.app_env.is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_env.is_local else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # exact origins; never "*"
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Organization", "X-Request-Id"],
        max_age=600,
    )

    @app.middleware("http")
    async def correlation_id(
        request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
    ) -> JSONResponse:
        """One id per request, on every log line and echoed to the client.

        A user reporting "it failed at 14:32" becomes a single grep.
        """
        try:
            request_id = uuid.UUID(request.headers.get("X-Request-Id", "")).hex
        except ValueError:
            request_id = uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "path")
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(OpsPilotError)
    async def handle_domain_error(_: Request, exc: OpsPilotError) -> JSONResponse:
        """Deliberate failures answer with a stable ``error_code``.

        Internal detail is logged, not returned: an error message is an oracle.
        """
        log.warning(
            "request.failed",
            error_code=exc.error_code,
            status=exc.status_code,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # SQL/provider exceptions may include parameters, prompts or credentials.
        log.error("request.unhandled", error=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"error_code": "internal.error", "message": "Request could not be completed."},
        )

    app.include_router(api_router)
    return app


app = create_app()
