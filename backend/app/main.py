"""FastAPI application factory. Run: uvicorn app.main:app --reload --app-dir backend"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.api import api_router
from app.api.lineage import router as lineage_router
from app.config import get_settings
from app.db import init_db
from app.observability import (
    RequestContextMiddleware,
    configure_logging,
    metrics_endpoint,
    request_id_var,
)

configure_logging(get_settings().log_format, get_settings().log_level)

log = logging.getLogger(__name__)

#: Body returned for any unhandled exception. Deliberately free of exception
#: text — the traceback goes to the server log, keyed by the request id (#282).
INTERNAL_ERROR_DETAIL = (
    "Internal server error. Quote the request id when reporting this to your administrator."
)


class UnhandledErrorMiddleware:
    """Turn any unhandled exception into a consistent JSON 500 (#282).

    Shape stays on FastAPI's ``{"detail": ...}`` convention so existing clients
    keep working, plus ``request_id`` in the body and ``X-Request-ID`` on the
    response so a user-reported error maps to a server-side traceback.

    Mounted *inside* :class:`RequestContextMiddleware` on purpose: Starlette's
    own ``ServerErrorMiddleware`` runs outermost, i.e. after the request-id
    contextvar has already been reset, so a handler registered there could not
    report the id. ``HTTPException`` and ``RequestValidationError`` never reach
    this middleware — Starlette's ``ExceptionMiddleware`` sits further in and
    handles those with their own status codes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def _send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception:
            request_id = request_id_var.get()
            route = scope.get("route")
            log.exception(
                "Unhandled exception on %s %s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                extra={
                    "event": "unhandled_exception",
                    "method": scope.get("method"),
                    "route": getattr(route, "path", scope.get("path")),
                    "status": 500,
                },
            )
            if started:
                # Response already on the wire; the status line can no longer change.
                raise
            response = JSONResponse(
                {"detail": INTERNAL_ERROR_DETAIL, "request_id": request_id},
                status_code=500,
                headers={"X-Request-ID": request_id},
            )
            await response(scope, receive, send)


def _warn_on_unused_llm_key(settings) -> None:
    """Say once, at startup, that a configured LLM key is going unused (#266).

    Silence here is how a paid key ends up doing nothing for weeks: every AI
    feature falls back (heuristics / 503) exactly as it does with no key at all.
    `llm_config_problem()` returns None when the LLM simply isn't configured, so
    the default no-key deployment stays quiet.
    """
    problem = settings.llm_config_problem()
    if problem:
        log.warning("LLM features are disabled: %s", problem, extra={"event": "llm_config_problem"})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    _warn_on_unused_llm_key(settings)
    app = FastAPI(
        title="DQ Sentinel API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/api/v1/openapi.json",
    )
    # Middleware order matters: add_middleware() prepends, so the LAST added is
    # outermost. Desired stack: CORS -> RequestContext -> UnhandledError -> routes,
    # which keeps the request-id contextvar alive while the 500 body is built.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(lineage_router, prefix="/api/v1")  # declares full /datasets + /connections paths

    @app.get("/api/v1/health", tags=["meta"])
    def health():
        from app.llm.client import provider_info

        return {
            "status": "ok",
            "version": __version__,
            "llm_enabled": settings.llm_enabled,
            # Why the LLM is off when a key IS configured; null when it is on, and
            # null when nothing is configured (#266). Names env vars only — never
            # any part of a key.
            "llm_disabled_reason": settings.llm_config_problem(),
            **provider_info(),
        }

    # Prometheus scrape target — unauthenticated by design (counts only, no data);
    # keep it network-internal in production.
    app.get("/metrics", include_in_schema=False)(metrics_endpoint)

    return app


app = create_app()
