FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DOCFLOW_DB_PATH=/data/docflow.db

COPY pyproject.toml README.md ./
COPY docflow ./docflow
RUN pip install --no-cache-dir ".[api,ui,mcp]"
COPY data ./data
COPY ui ./ui
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "docflow.api:app", "--host", "0.0.0.0", "--port", "8000"]
