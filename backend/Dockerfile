# Carbon Nexus — Cloud Run Dockerfile
# Base: Python 3.11 slim, optimized for cold start

FROM python:3.11-slim

# Metadata
LABEL org.opencontainers.image.title="Carbon Nexus API"
LABEL org.opencontainers.image.description="AI carbon footprint platform — Gemini + Vertex AI"

# Env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Install deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Non-root user (Cloud Run security best practice)
RUN adduser --disabled-password --gecos "" appuser
USER appuser

# Cloud Run expects PORT env var
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]