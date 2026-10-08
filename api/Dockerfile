# Build context: repository root (see deploy/docker-compose.yml)
FROM python:3.12-slim

WORKDIR /app
COPY common/ common/
COPY db/pyproject.toml db/
COPY db/scada_db/ db/scada_db/
COPY api/pyproject.toml api/
COPY api/app/ api/app/
RUN pip install --no-cache-dir ./common ./db ./api

WORKDIR /app/api
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
