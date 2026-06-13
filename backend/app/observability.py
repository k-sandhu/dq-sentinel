"""Logging + metrics for the whole stack (issue #44/#45). Fully open source:
stdlib logging (JSON formatter) + prometheus-client.

- configure_logging(): JSON or text logs to stdout (DQ_LOG_FORMAT), with
  request-ID correlation via a contextvar.
- RequestContextMiddleware: X-Request-ID in/out, one structured log line and
  HTTP metrics per request (route-template labels, not raw paths).
- Domain metrics: check runs, exceptions, worker claims, source queries, LLM
  calls/tokens. The API serves /metrics; the worker exposes a sidecar port.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# ---------------------------------------------------------------- metrics
HTTP_REQUESTS = Counter(
    "dq_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "dq_http_request_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
CHECK_RUNS = Counter(
    "dq_check_runs_total", "Check runs by outcome", ["status", "check_type", "trigger"]
)
CHECK_RUN_SECONDS = Histogram(
    "dq_check_run_seconds", "Check run duration", ["check_type"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
EXCEPTIONS_RECORDED = Counter(
    "dq_exceptions_recorded_total", "Exception rows captured by failed runs"
)
WORKER_CLAIMS = Counter("dq_worker_claims_total", "Checks claimed by the scheduler worker")
WORKER_UP = Gauge("dq_worker_up", "1 while a worker process is polling")
SOURCE_QUERIES = Counter(
    "dq_source_queries_total", "Queries issued to source databases", ["engine"]
)
SOURCE_QUERY_SECONDS = Histogram(
    "dq_source_query_seconds", "Source query latency", ["engine"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 15, 30),
)
LLM_REQUESTS = Counter(
    "dq_llm_requests_total", "LLM API calls", ["provider", "model", "outcome"]
)
LLM_LATENCY = Histogram(
    "dq_llm_request_seconds", "LLM call latency", ["provider", "model"],
    buckets=(0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80, 160),
)
LLM_TOKENS = Counter(
    "dq_llm_tokens_total", "LLM tokens used", ["provider", "model", "direction"]
)
NOTIFICATIONS_SENT = Counter(
    "dq_notifications_sent_total",
    "Notification deliveries by channel and outcome (issue #27)",
    ["channel", "outcome"],  # channel: slack|email ; outcome: success|failure
)


# ---------------------------------------------------------------- logging
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key in ("method", "route", "status", "duration_ms", "client", "event"):
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_var.get()
        prefix = f"[{rid}] " if rid != "-" else ""
        record.msg = f"{prefix}{record.msg}"
        return super().format(record)


def configure_logging(fmt: str = "text", level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log duplicates our request log line
    logging.getLogger("uvicorn.access").disabled = True


access_log = logging.getLogger("dq.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            duration = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            if route_path != "/metrics":  # scrapes would dominate the metrics
                HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
                HTTP_LATENCY.labels(request.method, route_path).observe(duration)
                access_log.info(
                    "%s %s -> %s",
                    request.method,
                    request.url.path,
                    status,
                    extra={
                        "method": request.method,
                        "route": route_path,
                        "status": status,
                        "duration_ms": round(duration * 1000, 2),
                        "client": request.client.host if request.client else None,
                        "event": "http_request",
                    },
                )
            request_id_var.reset(token)


def metrics_endpoint() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
