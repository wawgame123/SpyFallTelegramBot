FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 CMD python -m app.health
CMD ["python", "-m", "app.main"]
