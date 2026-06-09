# Multistage build: one image that serves the API + the built SPA on one port.
#
#   Stage 1 (node)   : build frontend/dist
#   Stage 2 (python) : install backend with uv, copy in dist, run uvicorn
#
# The container entrypoint applies DB migrations (alembic upgrade head) before
# starting the server — safe for the single-instance deployment we run.

# ---------- Stage 1: frontend build ----------
FROM node:22-slim AS frontend
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build
# -> /build/dist


# ---------- Stage 2: backend runtime ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install backend deps first, from lockfile, for layer caching.
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-install-project --no-dev

# Backend source + alembic.
COPY backend/ ./backend/

# Built SPA from stage 1. main.py reads FRONTEND_DIST to find it.
COPY --from=frontend /build/dist ./frontend/dist
ENV FRONTEND_DIST=/app/frontend/dist

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
WORKDIR /app/backend
ENTRYPOINT ["/entrypoint.sh"]
