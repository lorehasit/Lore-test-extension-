# Hugging Face Space (Docker SDK) image for the Lore backend.
FROM python:3.12-slim

# HF Spaces run as a non-root user (uid 1000).
RUN useradd -m -u 1000 user

# onnxruntime (fastembed) needs libgomp.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    # Non-secret config (secrets are set in the Space's Settings → Secrets):
    VECTOR_STORE=pgvector \
    GROQ_MODEL=llama-3.1-8b-instant \
    EMBEDDER_MODEL=BAAI/bge-small-en-v1.5 \
    EMBEDDER_DIMS=384

WORKDIR /app

COPY --chown=user requirements.txt requirements-pgvector.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt -r requirements-pgvector.txt

COPY --chown=user . .

# HF Spaces expect the app on port 7860.
EXPOSE 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
