# Hugging Face Space (Docker): всё в одном контейнере — Postgres, API, собранный фронтенд.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN apt-get -o Acquire::Retries=8 update && apt-get -o Acquire::Retries=8 install -y --no-install-recommends postgresql ffmpeg && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user && mkdir -p /data && chown user /data
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY --chown=user . .
COPY --from=web --chown=user /web/dist /app/frontend/dist
RUN chmod +x deploy/hf/start.sh
USER user
ENV PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 MEDIA_WORKERS=1 KEYFRAME_MAX=15 LLM_MAX_FRAMES=12 MEDIA_ROOT=/tmp/media APP_ENV=dev PIPELINE_MODE=inline \
    DATABASE_URL=postgresql+asyncpg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens \
    DATABASE_URL_SYNC=postgresql+psycopg://lessonlens:lessonlens@127.0.0.1:5432/lessonlens
EXPOSE 7860
CMD ["deploy/hf/start.sh"]
