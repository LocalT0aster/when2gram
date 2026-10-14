# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.14 AS uv

FROM python:3.14-alpine AS builder

COPY --from=uv /uv /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml README.md uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14-alpine AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    HOME="/home/when2gram" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apk upgrade --no-cache \
    && addgroup --system --gid 10001 when2gram \
    && adduser --system --uid 10001 --ingroup when2gram \
        --home /home/when2gram --shell /sbin/nologin when2gram \
    && mkdir /data \
    && chown when2gram:when2gram /data

COPY --from=builder /app/.venv /app/.venv
COPY migrations ./migrations
COPY alembic.ini ./

USER 10001:10001
VOLUME ["/data"]

CMD ["sh", "-c", "alembic upgrade head && exec python -m when2gram"]
