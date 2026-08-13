FROM python:3.12-slim

WORKDIR /app

# Dependencias de sistema necesarias para compilar paquetes con extensiones C
# (chromadb, tokenizers, sentence-transformers, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# La base de datos SQLite y ChromaDB viven en un volumen persistente (ver docker-compose.yml)
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/fitmind.db
ENV CHROMA_DB_PATH=/app/data/chroma_db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
