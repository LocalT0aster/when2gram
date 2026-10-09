# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:latest@sha256:dc1f306f26ba5ec796d32b4a30053a2f61192514d01c42278c8e5e0e90b11b22 AS uv

FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS builder

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml README.md uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN addgroup --system --gid 10001 when2gram \
    && adduser --system --uid 10001 --ingroup when2gram --home /app when2gram \
    && apk add --no-cache su-exec \
    && mkdir /data \
    && chown when2gram:when2gram /data

COPY --from=builder --chown=when2gram:when2gram /app/.venv /app/.venv
COPY --chown=when2gram:when2gram migrations ./migrations
COPY --chown=when2gram:when2gram alembic.ini ./
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint

VOLUME ["/data"]

ENTRYPOINT ["docker-entrypoint"]
CMD ["sh", "-c", "alembic upgrade head && exec python -m when2gram"]
