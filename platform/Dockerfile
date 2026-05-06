# Stage 1: Build web console
FROM node:22-slim AS frontend
WORKDIR /build
COPY web-console/package.json web-console/package-lock.json ./
RUN npm ci
COPY web-console/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION .
COPY api/ ./api/
COPY billing/ ./billing/
COPY --from=frontend /build/dist/ ./web-console/dist/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
