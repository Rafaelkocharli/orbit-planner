FROM node:24-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ORBIT_STORAGE_DIR=/app/storage
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY model/ model/
COPY data/ data/
COPY examples/ examples/
COPY backend/ backend/
COPY experiments/ experiments/
COPY --from=ui /ui/dist frontend/dist
# Runs live in memory with per-run locks, so the API must stay a single process.
RUN useradd --system --uid 10001 app && mkdir -p /app/storage && chown app /app/storage
USER app
VOLUME /app/storage
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
