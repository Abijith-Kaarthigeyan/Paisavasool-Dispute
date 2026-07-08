FROM python:3.11-slim-bookworm

# Copy uv binary from the official image (pin to a stable version to avoid latest checks)
COPY --from=ghcr.io/astral-sh/uv:0.5.21 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies using uv sync (caching the lock file details)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application
COPY . .

# Sync the project package itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

EXPOSE 8002
RUN sed -i 's/\r$//' /app/scripts/*.sh && chmod +x /app/scripts/*.sh
CMD ["/app/scripts/start_api.sh"]
