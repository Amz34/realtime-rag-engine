FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RAG_DB_PATH=/data/rag_vectors.db

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.162.2 \
    uvicorn==0.54.0 \
    pydantic==2.14.3 \
    numpy==2.4.2 \
    rank-bm25==0.2.2 \
    pytest==9.2.0

RUN mkdir -p /data

COPY engine.py /app/
COPY app.py /app/
COPY test_engine.py /app/
COPY simulate_accenture_benchmark.py /app/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/api/v1/health || exit 1

CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
