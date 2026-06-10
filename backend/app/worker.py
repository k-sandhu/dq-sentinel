"""Worker entrypoint: `python -m app.worker` (run from backend/ or with PYTHONPATH=backend)."""

from app.core.scheduler import run_forever
from app.db import init_db

if __name__ == "__main__":
    init_db()
    run_forever()
