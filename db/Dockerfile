# Build context: repository root (see deploy/docker-compose.yml)
FROM python:3.12-slim

# flaky venue Wi-Fi: retry PyPI instead of failing the whole build
ENV PIP_DEFAULT_TIMEOUT=60 PIP_RETRIES=10 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# 1) third-party dependencies: this layer depends only on pyproject.toml files, so code edits
#    (common/, services) rebuild from the cache without touching the network
COPY common/pyproject.toml common/
COPY db/pyproject.toml db/
RUN mkdir -p common/scada_common db/scada_db && touch common/scada_common/__init__.py db/scada_db/__init__.py && pip install ./common ./db

# 2) our code, reinstalled without dependencies (offline)
COPY common/scada_common/ common/scada_common/
COPY db/scada_db/ db/scada_db/
# drop setuptools build/ left by step 1: it holds the empty placeholder modules and is newer than the copied sources
RUN rm -rf */build && pip install --no-deps --force-reinstall ./common ./db

COPY deploy/seed/layout.geojson deploy/seed/fleet.json deploy/seed/
ENV SCADA_SEED_DIR=/app/deploy/seed
CMD ["python", "-m", "scada_db", "init"]
