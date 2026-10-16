#!/bin/bash
# Данные в Space не сохраняются между перезапусками: каждый старт — свежая база и демо-центр.
set -e
PG=$(ls -d /usr/lib/postgresql/*/bin | head -1)
export PGDATA=/tmp/pgdata
"$PG/initdb" -D "$PGDATA" -U postgres --auth=trust >/dev/null
"$PG/pg_ctl" -D "$PGDATA" -o "-k /tmp -c listen_addresses=127.0.0.1" -l /tmp/pg.log -w start
psql -h 127.0.0.1 -U postgres -q -c "CREATE USER lessonlens PASSWORD 'lessonlens';" -c "CREATE DATABASE lessonlens OWNER lessonlens;"
cd /app/backend
alembic upgrade head
python -m app.seed_demo --reset
exec uvicorn hf_app:app --app-dir /app/deploy/hf --host 0.0.0.0 --port 7860
