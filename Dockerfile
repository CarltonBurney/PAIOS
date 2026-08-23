# PAIOS development image.
#
# The package has no runtime dependencies — the governance core is deliberately
# pure Python — so this image exists to give the control plane a reproducible
# interpreter and a place to run the test suite alongside Postgres, Redis, and
# n8n.
#
# There is no API server to run yet. See docs/DEVELOPMENT.md.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency layer first so source edits do not invalidate the install.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e ".[dev]"

# Policy documents are read at runtime from the repository layout.
COPY policies/ ./policies/
COPY tests/ ./tests/

# Run as a non-root user; nothing here needs elevated privileges.
RUN useradd --create-home --uid 10001 paios \
    && chown -R paios:paios /app
USER paios

# No service to expose yet. Stay resident so the container can be exec'd into,
# and run the suite explicitly with `docker compose run --rm paios pytest`.
CMD ["sleep", "infinity"]
