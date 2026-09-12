# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.13.15
ARG UV_VERSION=0.12.4

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim-trixie AS python-base

FROM python-base AS builder
ARG PYTHON_VERSION
COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    UV_PYTHON_DOWNLOADS=0
COPY .python-version pyproject.toml uv.lock ./
COPY services/orchestrator/pyproject.toml services/orchestrator/pyproject.toml
COPY services/worker/pyproject.toml services/worker/pyproject.toml
COPY services/platform_migrations/pyproject.toml services/platform_migrations/pyproject.toml
COPY libs/platform_db/pyproject.toml libs/platform_db/pyproject.toml
COPY libs/ctc_database/pyproject.toml libs/ctc_database/pyproject.toml
COPY libs/platform_observability/pyproject.toml libs/platform_observability/pyproject.toml
RUN test "$(tr -d '\r\n' < .python-version)" = "${PYTHON_VERSION}"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package worker
COPY services/worker services/worker
COPY libs/platform_db libs/platform_db
COPY libs/ctc_database libs/ctc_database
COPY libs/platform_observability libs/platform_observability
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package worker

FROM python-base AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
WORKDIR /app
ENV HOME=/home/appuser \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" appuser \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home \
        --home-dir /home/appuser --shell /usr/sbin/nologin --no-log-init appuser
COPY --from=builder /app/.venv /app/.venv
COPY --chown=${APP_UID}:${APP_GID} config /app/config
USER ${APP_UID}:${APP_GID}
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD python -c "import os,pathlib; assert any('worker.main' in p.read_text(errors='ignore') for p in pathlib.Path('/proc').glob('[0-9]*/cmdline') if p.parts[-2] != str(os.getpid()))"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/.venv/bin/python", "-m", "worker.main"]
