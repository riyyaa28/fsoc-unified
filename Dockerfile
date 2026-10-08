# Build the React/Three.js client first, then serve it from the same FastAPI
# process that owns the WebSocket simulation state.
FROM node:20-bookworm-slim AS web-build

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

# FSOC_STREAM_HZ caps telemetry at 10 updates/s to limit hosting bandwidth (the desktop app is uncapped).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FSOC_STREAM_HZ=10

WORKDIR /app

# Runtime libraries required by OpenCV's headless wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . ./
COPY --from=web-build /build/web/dist ./web/dist

# Render provides PORT (normally 10000).  Bind publicly inside the container.
CMD ["sh", "-c", "uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-10000}"]
