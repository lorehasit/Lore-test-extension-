# Container image for the Lore backend (used by Fly.io).
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# onnxruntime (used by fastembed) needs libgomp.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install deps first (better layer caching). pgvector deps included for prod.
COPY requirements.txt requirements-pgvector.txt ./
RUN pip install -r requirements.txt -r requirements-pgvector.txt

COPY . .

EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
