"""Worker entrypoint: `python -m app.worker` (run from backend/ or with PYTHONPATH=backend).

Exposes Prometheus metrics on DQ_WORKER_METRICS_PORT (default 9100) so the
scheduler's activity is observable alongside the API.
"""

from prometheus_client import start_http_server

from app.config import get_settings
from app.core.scheduler import run_forever
from app.db import init_db
from app.observability import configure_logging

if __name__ == "__main__":
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    start_http_server(settings.worker_metrics_port)
    init_db()
    run_forever()
