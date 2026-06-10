"""FastAPI application factory. Run: uvicorn app.main:app --reload --app-dir backend"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import api_router
from app.config import get_settings
from app.db import init_db
from app.observability import RequestContextMiddleware, configure_logging, metrics_endpoint

configure_logging(get_settings().log_format, get_settings().log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DQ Sentinel API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/api/v1/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/api/v1/health", tags=["meta"])
    def health():
        from app.llm.client import provider_info

        return {
            "status": "ok",
            "version": __version__,
            "llm_enabled": settings.llm_enabled,
            **provider_info(),
        }

    # Prometheus scrape target — unauthenticated by design (counts only, no data);
    # keep it network-internal in production.
    app.get("/metrics", include_in_schema=False)(metrics_endpoint)

    return app


app = create_app()
