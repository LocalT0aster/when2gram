FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml README.md uv.lock ./
RUN uv sync --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --no-dev

RUN mkdir -p /data
VOLUME ["/data"]

CMD ["sh", "-c", "uv run alembic upgrade head && exec uv run python -m when2gram"]
