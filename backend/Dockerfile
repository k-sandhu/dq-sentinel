FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Container default for the built-in data catalog (#261): the code default
# (<repo>/samples/catalog, which resolves to /samples/catalog in this image) is
# outside the app dir and not writable by the non-root user below. compose mounts
# a shared named volume here so api and worker generate/see the SAME files.
ENV DQ_CATALOG_DATA_DIR=/data/catalog

# Non-root runtime user (#292). Created before the installs so the chown below is
# the only ownership pass we pay for.
RUN useradd --system --create-home --uid 10001 --shell /usr/sbin/nologin dq

# ---- dependency layer (reproducible + cached) --------------------------------
# Install the PINNED dependency set first (#290): pyproject declares `>=` floors,
# so resolving at build time made two builds days apart differ. This layer is
# rebuilt only when requirements.lock changes — editing app code no longer busts it.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# ---- application layer -------------------------------------------------------
COPY pyproject.toml alembic.ini ./
COPY app ./app
# Migrations run at startup via init_db() (Alembic); ship them in the image.
COPY migrations ./migrations
# Install the local package itself WITHOUT re-resolving dependencies, so the
# pinned set above is exactly what ends up in the image. (Build isolation fetches
# setuptools into a throwaway venv; it never lands in the runtime image.)
RUN pip install --no-cache-dir --no-deps . \
    && mkdir -p /data/catalog \
    && chown -R dq:dq /app /data

USER dq

EXPOSE 8000

# Liveness for compose/orchestrators. stdlib only — this image has no curl/wget,
# and adding one just for the probe would grow the attack surface.
# The worker service has no HTTP API; it overrides this in docker-compose.yml.
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).read()"]

# API by default; the worker service overrides the command (see docker-compose.yml)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
