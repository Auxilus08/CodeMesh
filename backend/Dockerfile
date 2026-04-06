# ─── Stage 1: Python Backend ──────────────────────────────────────────────────
FROM python:3.12-slim AS backend

LABEL maintainer="Echo Team"
LABEL description="Echo — Decentralized LAN Chat Backend Daemon"

WORKDIR /app/backend

# Install deps first (layer cache friendly)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ .

# Expose the WebSocket bridge port (for frontend ↔ daemon communication)
EXPOSE 8765

# Run the discovery daemon by default (will be extended as more modules land)
CMD ["python", "-m", "discovery"]
