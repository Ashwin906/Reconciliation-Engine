# Multi-stage Dockerfile for the Reconciliation Engine
# Stage 1: Build Vite / React frontend dashboard
FROM node:20-slim AS frontend-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

# Stage 2: Python FastAPI backend serving the complete engine + SPA
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY core/ core/
COPY gen/ gen/
COPY ingest/ ingest/
COPY match/ match/
COPY verify/ verify/
COPY invariant/ invariant/
COPY detect/ detect/
COPY agent/ agent/
COPY gate/ gate/
COPY ledger/ ledger/
COPY evalh/ evalh/
COPY pipeline/ pipeline/
COPY reserve/ reserve/
COPY sim/ sim/
COPY api/ api/
COPY landing.html intro-video.html pitch-script.html ./

# Copy compiled frontend from Stage 1
COPY --from=frontend-build /app/web/dist /app/web/dist

RUN pip install --no-cache-dir .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
