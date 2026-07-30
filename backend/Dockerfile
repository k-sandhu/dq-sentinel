FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# /app is the application ROOT, not merely the working directory: app/config.py
# derives BACKEND_DIR from its own __file__ and app/db.py loads
# BACKEND_DIR/alembic.ini, so the source-tree layout (/app/app + /app/alembic.ini
# + /app/migrations) is what makes the startup migration run resolve. Say that on
# sys.path explicitly rather than relying on "cwd wins" (#310) — then
# `docker exec <c> python -m app.worker` imports the same code from any cwd.
ENV PYTHONPATH=/app

# Container default for the built-in data catalog (#261): the code default
# (<repo>/samples/catalog, which resolves to /samples/catalog in this image) is
# outside the app dir and not writable by the non-root user below. compose mounts
# a shared named volume here so api and worker generate/see the SAME files.
ENV DQ_CATALOG_DATA_DIR=/data/catalog

# Non-root runtime user (#292). Created before the copies so COPY --chown below
# can assign ownership as it writes, instead of duplicating the tree in a
# `chown -R` layer.
RUN useradd --system --create-home --uid 10001 --shell /usr/sbin/nologin dq

# ---- dependency layer (reproducible + cached) --------------------------------
# Install the PINNED dependency set first (#290): pyproject declares `>=` floors,
# so resolving at build time made two builds days apart differ. This layer is
# rebuilt only when requirements.lock changes — editing app code no longer busts it.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# ---- application layer -------------------------------------------------------
# The app is deliberately NOT pip-installed; it runs from this source tree.
# It used to be BOTH copied here and `pip install --no-deps .`-ed, which put
# three copies of the package in the image (/app/app, site-packages/app, and
# setuptools' leftover /app/build/lib/app) while only /app/app was ever imported
# (#310). Collapsing the other way — install into site-packages, drop the COPY —
# is not available: BACKEND_DIR (see above) would resolve to site-packages, where
# alembic.ini and migrations/ do not live, so init_db() could not migrate. That
# also retires the last unpinned build input (build isolation downloaded
# setuptools from PyPI at build time, against the reproducibility goal of #290)
# and with it the SetuptoolsWarning about the README this context never copied.
# If the app ever needs its own distribution metadata (importlib.metadata), add
# the install back — and keep alembic.ini/migrations next to the package.
COPY --chown=dq:dq alembic.ini ./
COPY --chown=dq:dq app ./app
# Migrations run at startup via init_db() (Alembic); ship them in the image.
COPY --chown=dq:dq migrations ./migrations

# /app itself must belong to dq: on the default (SQLite) settings the app DB is
# created at BACKEND_DIR/dqsentinel.db, i.e. inside this directory.
RUN mkdir -p /data/catalog && chown dq:dq /app /data /data/catalog

USER dq

EXPOSE 8000

# Liveness for compose/orchestrators. stdlib only — this image has no curl/wget,
# and adding one just for the probe would grow the attack surface.
# The worker service has no HTTP API; it overrides this in docker-compose.yml.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).read()"]

# API by default; the worker service overrides the command (see docker-compose.yml)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
