# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Non-root runtime.
RUN useradd --create-home --uid 10001 agent
USER agent

# HTTP health/metrics surface.
EXPOSE 8099

ENTRYPOINT ["agent"]
CMD ["up"]
