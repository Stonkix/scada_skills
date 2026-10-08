# Build context: repository root (see deploy/docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app
COPY common/ common/
COPY db/pyproject.toml db/
COPY db/scada_db/ db/scada_db/
COPY connectors/pyproject.toml connectors/
COPY connectors/scada_connectors/ connectors/scada_connectors/
COPY simulator/pyproject.toml simulator/
COPY simulator/scada_simulator/ simulator/scada_simulator/
RUN pip install --no-cache-dir ./common ./db ./connectors ./simulator

COPY simulator/scenarios.yaml simulator/scenarios.yaml
ENV SCENARIOS_PATH=/app/simulator/scenarios.yaml
EXPOSE 8010
CMD ["uvicorn", "scada_simulator.main:app", "--host", "0.0.0.0", "--port", "8010"]
