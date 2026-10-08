FROM python:3.11-slim

# System deps: FFmpeg, ffprobe, fonts for PIL captions
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-open-sans \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Arial Black equivalent — Carlito/Liberation for caption rendering
RUN fc-cache -fv 2>/dev/null || true

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY scripts/ ./scripts/
COPY config/.env.example ./config/.env.example

# Data dirs (overridden by volume mounts in docker-compose)
RUN mkdir -p data/clips data/vods data/state logs

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "scripts/telegram_bot.py"]
