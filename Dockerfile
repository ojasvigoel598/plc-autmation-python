# Multi-stage build: compile the React 3D digital twin, then package it with
# the Python SCADA backend so the whole plant runs from a single container.
#
#   docker build -t plc-scada-sim .
#   docker run -p 8000:8000 -p 502:502 plc-scada-sim
#
# The browser HMI is served by FastAPI at /app (the committed frontend/dist is
# regenerated here from source so the image always matches the current UI).

# ---- stage 1: build the React/Three.js frontend ---------------------------
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- stage 2: run the Python simulation + SCADA backend --------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY scada/ ./scada/
COPY run_scada.py ./
COPY --from=frontend /build/frontend/dist ./frontend/dist

# Persisted leak events and SQLite trends live outside the image so history
# survives container restarts.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# 8000 = browser HMI/REST/WebSocket, 502 = Modbus TCP field interface.
# The port is taken from the standard $PORT env var when set (Render, Fly.io,
# Railway) and defaults to 8000 otherwise (docker compose, local runs).
EXPOSE 8000 502

CMD ["python", "run_scada.py", "--host", "0.0.0.0"]
