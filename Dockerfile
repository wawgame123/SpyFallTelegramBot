FROM node:24-alpine AS frontend
WORKDIR /frontend
COPY app/views/web/package.json app/views/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY app/views/web/ ./
RUN npm run build

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY --from=frontend /frontend/dist/ ./app/views/web/dist/
COPY starter_packs.sqlite.sql .

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD python -m app.health

CMD ["python", "-m", "app.main"]
