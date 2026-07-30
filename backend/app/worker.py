"""Worker entrypoint: `python -m app.worker` (run from backend/ or with PYTHONPATH=backend).

Exposes Prometheus metrics on DQ_WORKER_METRICS_PORT (default 9100) so the
scheduler's activity is observable alongside the API.

Also serves the container health probe: `python -m app.worker --healthcheck`
exits 0 only when the scheduler loop is actually running (#311). The metrics
*port* is deliberately bound before migrations run, so an open port proves
nothing about readiness — see :data:`READY_METRIC`.
"""

import sys
import urllib.request

from prometheus_client import start_http_server

from app.config import get_settings

# The readiness signal. app/core/scheduler.py sets this gauge to 1 only after
# init_db() has returned and run_forever() has been entered, and back to 0 once
# the loop drains — so it distinguishes "the metrics port is open" (true from the
# first second, even while a migration is wedged) from "this worker can claim a
# due check". The healthcheck greps for it rather than for a 200 on /metrics.
READY_METRIC = "dq_worker_up"


def _readiness_ok(metrics_body: str) -> bool:
    """True when Prometheus exposition text reports ``READY_METRIC`` as 1."""
    prefix = f"{READY_METRIC} "
    for line in metrics_body.splitlines():
        if line.startswith(prefix):
            try:
                return float(line.split()[1]) == 1.0
            except (IndexError, ValueError):
                return False
    return False  # gauge absent entirely -> not ready


def healthcheck(port: int, timeout: float = 4.0) -> bool:
    """Probe this worker's own metrics endpoint and report readiness.

    A refused connection / timeout is "not ready", not an error: during the first
    moments of boot the port genuinely is not bound yet.
    """
    url = f"http://127.0.0.1:{port}/metrics"
    # Empty ProxyHandler: this is a loopback self-probe, and a deployment that
    # sets HTTP_PROXY in the container env must not have it routed outbound.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except OSError:  # URLError/HTTPError/socket timeout all derive from OSError
        return False
    return _readiness_ok(body)


def main(argv: list[str] | None = None) -> int:
    """Run the scheduler, or (with ``--healthcheck``) probe a running one."""
    args = sys.argv[1:] if argv is None else list(argv)

    if args == ["--healthcheck"]:
        return 0 if healthcheck(get_settings().worker_metrics_port) else 1
    if args:
        # Exact match above, so a typo'd probe flag can never fall through and
        # start a *second* scheduler from inside a healthcheck. stderr rather than
        # a logger: this is CLI usage, and logging is not configured yet.
        sys.stderr.write(f"usage: python -m app.worker [--healthcheck] (unrecognized: {args})\n")
        return 2

    settings = get_settings()
    # Deferred on purpose: importing the scheduler pulls in the whole check/ML
    # stack (~2s). The --healthcheck path above runs every 15s in the container
    # against a 5s probe timeout, and must not pay for it.
    from app.core.scheduler import run_forever
    from app.db import init_db
    from app.observability import configure_logging

    configure_logging(settings.log_format, settings.log_level)
    # Bind the metrics port BEFORE migrating. A worker wedged in init_db() (say,
    # waiting on the migration advisory lock) is precisely when an operator wants
    # a scrapeable target and a live process to look at; starting the server
    # after the migration would leave that window dark. The cost — an endpoint
    # that answers before the worker can do any work — is paid for by READY_METRIC.
    start_http_server(settings.worker_metrics_port)
    init_db()
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
