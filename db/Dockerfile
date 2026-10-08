# Build context: repository root (see deploy/docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app
COPY common/ common/
COPY db/pyproject.toml db/
COPY db/scada_db/ db/scada_db/
RUN pip install --no-cache-dir ./common ./db

COPY deploy/seed/layout.geojson deploy/seed/fleet.json deploy/seed/
ENV SCADA_SEED_DIR=/app/deploy/seed

CMD ["python", "-m", "scada_db", "init"]
