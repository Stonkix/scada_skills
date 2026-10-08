# Build context: repository root (see deploy/docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app
COPY common/ common/
COPY db/pyproject.toml db/
COPY db/scada_db/ db/scada_db/
COPY worker/pyproject.toml worker/
COPY worker/scada_worker/ worker/scada_worker/
RUN pip install --no-cache-dir ./common ./db ./worker

CMD ["python", "-m", "scada_worker.main"]
