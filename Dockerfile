# Backend: FastAPI + the whole reconciliation engine.
# [UNVERIFIED end-to-end] -- Docker Desktop was not running while this
# repo was built (same constraint noted throughout core/schema.sql,
# ingest/store.py, ledger/store.py: no live infrastructure available).
# The image build steps below are standard and the app itself is proven
# by 340+ passing tests; what's untested is the CONTAINERIZED run itself.
# First thing to verify once Docker is available: `docker compose up`
# actually serves both /docs and the dashboard.

FROM python:3.12-slim

WORKDIR /app

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
COPY api/ api/

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
