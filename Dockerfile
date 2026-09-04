ARG NODE_IMAGE=node:24.14.1-alpine
ARG PYTHON_IMAGE=python:3.11-slim

# Frontend output is architecture-neutral. Build it on the native runner so
# arm64 Gateway images do not run npm/Node through QEMU.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY contracts/ /src/contracts/
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS runtime

ARG SIMING_VERSION=dev
LABEL org.opencontainers.image.title="Siming Gateway" \
      org.opencontainers.image.description="User-owned synchronization and cloud AI Gateway for Siming" \
      org.opencontainers.image.source="https://github.com/teangtang1122/siming-ai" \
      org.opencontainers.image.version="${SIMING_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    SIMING_RUNTIME_PROFILE=gateway \
    SIMING_GATEWAY_HEADLESS=true \
    SIMING_HOME=/data \
    SIMING_CONTENT_ROOT=/data/projects \
    DATABASE_URL=sqlite:////data/siming.db

WORKDIR /app
COPY backend/requirements-gateway.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && addgroup --system --gid 10001 siming \
    && adduser --system --uid 10001 --ingroup siming --home /data siming \
    && mkdir -p /data/projects /app/frontend \
    && chown -R siming:siming /data

COPY backend/ /app/backend/
COPY --from=frontend-build /src/frontend/dist/ /app/frontend/dist/

USER siming
WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)); assert data['status'] in {'healthy','recovery'}"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
