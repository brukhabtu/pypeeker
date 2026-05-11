# syntax=docker/dockerfile:1.7
# Base image for running pypeeker's test suite hermetically in CI.
#
# Build:    docker build -t pypeeker .
# Run:      docker run --rm pypeeker            # default: uv run pytest
# Override: docker run --rm pypeeker uv run ... # any uv-driven command

FROM python:3.14-slim

# Pin and copy uv from its official image — avoids a curl-and-pipe install
# and gives us a deterministic uv version.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# tree-sitter has C extensions; the slim base lacks compilers. Install just
# what we need to build wheels, then drop the apt cache.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dep layer: copy project metadata first so this caches when only source
# changes. uv resolves and installs the locked deps but not the project itself.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Source: copy the project + tests, then install the project itself.
COPY src/ ./src/
COPY tests/ ./tests/
RUN uv sync --frozen

# Pre-build the semantic index — this is what `.semantic-tool/index/` would
# contain locally. Self-validation tests need it on disk. Doing this at
# image build time means every container starts ready to test.
RUN uv run pypeeker index src/

CMD ["uv", "run", "pytest"]
