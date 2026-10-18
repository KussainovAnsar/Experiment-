#!/bin/bash
# Один контейнер: Postgres + API + SPA.
# По умолчанию (Render, HF) данные не сохраняются: каждый старт — свежая база и демо-центр.
# PERSIST_DATA=1 и том на /data — база и загрузки переживают перезапуск, демо заливается только при первом старте.
set -e
PG=$(ls -d /usr/lib/postgresql/*/bin | head -1)
PG_OPTS="-k /tmp -c listen_addresses=127.0.0.1 -c shared_buffers=${PG_SHARED_BUFFERS:-16MB} -c max_connections=20 -c work_mem=2MB"
FIRST_RUN=1
if [ "${PERSIST_DATA:-0}" = "1" ]; then
  export PGDATA=/data/pgdata MEDIA_ROOT=/data/media
  mkdir -p "$MEDIA_ROOT"
  [ -s "$PGDATA/PG_VERSION" ] && FIRST_RUN=0
  rm -f "$PGDATA/postmaster.pid"   # остался от аварийной остановки контейнера
else
  export PGDATA=/tmp/pgdata
  rm -rf "$PGDATA"   # после рестарта контейнера на том же инстансе каталог остаётся — initdb упал бы
fi
if [ "$FIRST_RUN" = "1" ]; then
  "$PG/initdb" -D "$PGDATA" -U postgres --auth=trust >/dev/null
fi
"$PG/pg_ctl" -D "$PGDATA" -o "$PG_OPTS" -l /tmp/pg.log -w start
if [ "$FIRST_RUN" = "1" ]; then
  psql -h 127.0.0.1 -U postgres -q -c "CREATE USER lessonlens PASSWORD 'lessonlens';" -c "CREATE DATABASE lessonlens OWNER lessonlens;"
fi
cd /app/backend
alembic upgrade head
if [ "$FIRST_RUN" = "1" ]; then
  python -m app.seed_demo --reset
fi
exec uvicorn hf_app:app --app-dir /app/deploy/hf --host 0.0.0.0 --port "${PORT:-7860}"
