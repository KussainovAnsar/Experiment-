# LessonLens API / worker image. Build context = repository root (see docker-compose.yml):
#   docker build -f backend/Dockerfile -t lessonlens-api .
# Layout inside the image mirrors the repo so app.config.REPO_DIR resolves the demo fixtures:
#   /srv/backend  (this directory)      /srv/demo (fixtures)      /srv/media (volume)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEDIA_ROOT=/srv/media

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libpq5 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ /srv/backend/
COPY demo/ /srv/demo/

RUN mkdir -p /srv/media
VOLUME ["/srv/media"]

EXPOSE 8000
# The API container runs migrations first (docker-compose overrides the command for the worker).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
