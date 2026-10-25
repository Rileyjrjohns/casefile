FROM ghcr.io/astral-sh/uv:0.11.26 AS uv
FROM python:3.12.11-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=0 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY models ./models
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev && useradd --uid 10001 --create-home casefile
USER casefile
EXPOSE 8000
CMD ["/app/.venv/bin/casefile-serve", "--host", "0.0.0.0"]
