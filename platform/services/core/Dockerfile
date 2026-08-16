# ---------------------------------------------------------------------------
# jarvis-core
#
# The base image names its Debian release ON PURPOSE. `python:3.12-slim` is a
# floating tag: Pimlico's browser-agent image was built from it, the tag moved
# to a newer Debian where `libasound2` had been renamed, and the Dockerfile
# stopped building — so the running image could no longer be rebuilt from its
# own source. That was discovered mid-incident, which is the worst possible
# time. Pin the release; upgrade it deliberately.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY migrations/ /app/migrations/
# Tests and their config ship INSIDE the image (02-ARCHITECTURE §10: contract
# tests run in the deployed container, against the real service). A suite that
# only exists on a build host cannot tell you anything about what is running.
COPY tests/ /app/tests/
COPY pytest.ini /app/pytest.ini

# `jpd` on PATH inside the container, so the operator runs the SAME binary the
# service runs. Pimlico had host scripts that drifted from container code.
RUN printf '#!/bin/sh\nexec python -m jarvis.cli "$@"\n' > /usr/local/bin/jpd \
 && chmod +x /usr/local/bin/jpd

RUN mkdir -p /app/data/artifacts

ENV JPD_PACKAGE_ROOT=/app

# Import check at BUILD time, not deploy time.
#
# 🔴 A syntax check is not an import check. A hermes change once passed
# `py_compile` while using `Optional` with no `typing` import — annotations
# evaluate at def time, so it raised NameError at import and would have
# crash-looped the container. `import` catches what `py_compile` cannot.
RUN python -c "import jarvis, jarvis.cli, jarvis.main, jarvis.db, jarvis.config; \
import jarvis.runtime.engine, jarvis.runtime.registry, jarvis.runtime.checkpoints; \
import jarvis.runtime.lease, jarvis.runtime.watermark, jarvis.runtime.types; \
import jarvis.connectors.base; print('import check ok')"

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8900/health || exit 1

EXPOSE 8900

# --workers 1 is LOAD-BEARING. See src/jarvis/main.py — multiple in-process
# workers put every gauge in a per-worker registry and make service-level
# alerting impossible. Scale with swarm replicas instead.
CMD ["uvicorn", "jarvis.main:app", "--host", "0.0.0.0", "--port", "8900", "--workers", "1"]
