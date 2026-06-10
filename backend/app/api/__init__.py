from fastapi import APIRouter

from app.api import (
    adhoc_dashboards,
    auth,
    checks,
    connections,
    dashboard,
    datasets,
    exceptions_api,
    knowledge,
    mcp,
    query,
    rca,
    runs,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(connections.router)
api_router.include_router(datasets.router)
api_router.include_router(knowledge.router)
api_router.include_router(checks.router)
api_router.include_router(runs.router)
api_router.include_router(exceptions_api.router)
api_router.include_router(rca.router)
api_router.include_router(dashboard.router)
api_router.include_router(query.router)
api_router.include_router(adhoc_dashboards.router)
api_router.include_router(mcp.router)
