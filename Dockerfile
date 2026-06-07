FROM python:3.11-slim

WORKDIR /app

# curl is needed for the ALB health check
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install only the runtime deps (no DB or test packages)
COPY backend/requirements-demo.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ ./backend/
COPY Dataset.csv ./Dataset.csv

# Python package path
ENV PYTHONPATH=/app/backend

# Defaults — all can be overridden via ECS task definition env vars
ENV DATASET_CSV_PATH=/app/Dataset.csv \
    LLM_PROVIDER=mock \
    MESSAGE_CHANNEL=mock \
    VOICE_PROVIDER=mock \
    EVENT_BUS=memory

EXPOSE 8000

# ALB health check (30 s interval, 90 s start period for CSV load)
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Single worker — in-memory call sessions cannot be shared across workers
CMD ["uvicorn", "pulselink.demo.api:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
