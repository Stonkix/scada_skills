# Build context: repository root (see deploy/docker-compose.yml)
FROM python:3.12-slim

# flaky venue Wi-Fi: retry PyPI instead of failing the whole build
ENV PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=10 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# 1) third-party dependencies: this layer depends only on pyproject.toml files, so code edits
#    (common/, services) rebuild from the cache without touching the network
COPY common/pyproject.toml common/
COPY db/pyproject.toml db/
COPY connectors/pyproject.toml connectors/
RUN mkdir -p common/scada_common db/scada_db connectors/scada_connectors && touch common/scada_common/__init__.py db/scada_db/__init__.py connectors/scada_connectors/__init__.py && pip install ./common ./db ./connectors

# 2) our code, reinstalled without dependencies (offline)
COPY common/scada_common/ common/scada_common/
COPY db/scada_db/ db/scada_db/
COPY connectors/scada_connectors/ connectors/scada_connectors/
# drop setuptools build/ left by step 1: it holds the empty placeholder modules and is newer than the copied sources
RUN rm -rf */build && pip install --no-deps --force-reinstall ./common ./db ./connectors
EXPOSE 8001
CMD ["uvicorn", "scada_connectors.main:app", "--host", "0.0.0.0", "--port", "8001"]
