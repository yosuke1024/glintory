# Dockerfile
FROM python:3.12-slim-bookworm AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy application source
COPY src/ /app/src/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini
COPY pyproject.toml uv.lock /app/

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim-bookworm

WORKDIR /app

# Create a non-root user and setup database volume directory
RUN groupadd -r glintory && useradd -r -g glintory glintory && mkdir /data && chown -R glintory:glintory /data

# Copy virtual environment and app source from builder
COPY --from=builder --chown=glintory:glintory /app /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PORT=8000
ENV PYTHONPATH="/app/src"

# Set environment variables for production execution
ENV GLINTORY_HOST=0.0.0.0
ENV GLINTORY_PORT=$PORT

EXPOSE $PORT

USER glintory

# Start FastAPI server using uvicorn (single process, no --reload)
CMD ["sh", "-c", "uvicorn glintory.main:app --host 0.0.0.0 --port $PORT --workers 1"]
