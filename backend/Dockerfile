FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer caching).
# Open-core: the private gateway repo adds backend/requirements-gateway.txt
# (Stripe, etc.); a plain core checkout has no such file and skips that step.
COPY backend/requirements*.txt /app/backend/
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
 && if [ -f /app/backend/requirements-gateway.txt ]; then \
      pip install --no-cache-dir -r /app/backend/requirements-gateway.txt; \
    fi

# Copy application code
COPY backend/ /app/backend/

# Copy runtime assets needed by the backend
# Taxonomy: fallback for local mode; GCS mode downloads from bucket
COPY taxonomy/ /app/taxonomy/

# Changelog: single source of truth for the user-facing Pinscope version.
# Read by backend/_version.py at startup and stamped onto each new pipeline run.
# Staged into backend/ by cloudbuild before this step runs so the broad
# `frontend/` exclude in .dockerignore doesn't block the COPY.
COPY backend/_changelog.md /app/changelog.md

EXPOSE 8080

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
