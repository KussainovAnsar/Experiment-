# LessonLens — технический контракт MVP

Рабочее название продукта: **LessonLens** — AI Quality & Learning Analytics для онлайн-репетиторских центров (Казахстан).
Этот документ — единый источник правды для всех, кто пишет код в репозитории. Если контракт и код расходятся — побеждает контракт, либо контракт правится явно.

## 1. Что собираем (MVP, версия 1 «Post-Lesson Intelligence»)

Пилотный сценарий: онлайн-урок **русского языка в казахской школе**, формат один преподаватель — один ученик, запись Zoom/Meet (видео + аудио).

Пайплайн после загрузки записи:

1. Загрузка видео/аудио урока (веб-форма центра) → `lessons.status = uploaded`.
2. `ffprobe` → метаданные; `ffmpeg` → `audio.wav` (16 kHz mono).
3. STT с таймкодами и разделением говорящих (учитель/ученик) → `transcripts`.
4. Ключевые кадры видео: смена сцены + периодическая выборка, дедупликация, ≤ `KEYFRAME_MAX` → `frames`.
   Для русского языка визуальная часть критична: доска/экран с правилом, упражнение, написанное учеником слово, ошибки в орфографии.
5. Мультимодальный LLM-анализ (транскрипт с таймкодами + кадры с таймкодами) → строго типизированный JSON `LessonAnalysis` (см. §5) → `analyses`.
6. Три отчёта из одного анализа: преподавателю, руководителю/методисту, родителю → `reports` (kind = teacher | manager | parent).
7. Обновление истории прогресса ученика (`skill_states`), уведомление родителю (Telegram; WhatsApp — заглушка) + ссылка в родительский кабинет.

Не делаем в MVP: авто-подключение бота к Zoom/Meet, realtime-подсказки, рейтинги преподавателей, биллинг.

## 2. Стек и запуск

| Слой | Технологии |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Alembic, pydantic v2, pydantic-settings, httpx, structlog |
| Очередь | Таблица `jobs` в PostgreSQL + воркер (`python -m app.worker`, `FOR UPDATE SKIP LOCKED`). Режим `PIPELINE_MODE=inline` запускает обработку фоновой задачей в API-процессе (для демо). |
| Медиа | ffmpeg/ffprobe через `subprocess`, Pillow для кадров |
| STT | адаптеры: mock, whisper_local (faster-whisper), openai, deepgram, assemblyai, google, elevenlabs, yandex |
| LLM | адаптеры: mock, anthropic (по умолчанию `claude-sonnet-5-5`, мультимодально), openai |
| Мессенджеры | Telegram (aiogram 3, polling или webhook), WhatsApp (Meta Cloud API — заготовка интерфейса), mock |
| Frontend | React 18 + Vite + TypeScript, react-router, TanStack Query, без тяжёлых UI-китов (свой минимальный CSS, светлая тема, русский интерфейс) |
| БД | PostgreSQL 16 |
| Тесты | pytest + pytest-asyncio (backend, против реального Postgres `lessonlens_test`), vitest (frontend, минимум), Playwright e2e (smoke) |

Локально: Postgres на `127.0.0.1:5432`, пользователь/пароль/БД `lessonlens`, тестовая БД `lessonlens_test`. Python-окружение: `backend/.venv`. Фронтенд: `frontend/` (`npm run dev` на 5173, проксирует `/api` на 8000).

Команды (должны работать после завершения MVP):

```bash
# backend
cd backend && source .venv/bin/activate
alembic upgrade head
python -m app.seed_demo            # демо-центр, учитель, ученик, родитель, один обработанный урок
uvicorn app.main:app --reload --port 8000
python -m app.worker               # только при PIPELINE_MODE=worker
pytest
# frontend
cd frontend && npm install && npm run dev
```

## 3. Структура репозитория

```
backend/
  app/
    main.py            — создание FastAPI, роутеры, CORS, lifespan (старт бота в polling-режиме, если токен задан)
    config.py          — Settings (pydantic-settings), читает .env из корня репо и backend/
    logging.py         — structlog
    db/
      base.py          — DeclarativeBase, naming convention
      session.py       — async engine/sessionmaker, get_session dependency
      models.py        — ВСЕ модели (§4). Единственное место.
    migrations/        — alembic (env.py с sync psycopg URL)
    schemas/
      analysis.py      — LessonAnalysis и вложенные модели (§5). Единственное место.
      api.py           — Pydantic DTO для API (§6)
    api/
      deps.py          — auth-зависимости: current_staff, current_parent, require_role
      auth.py          — /api/auth/*
      centers.py       — /api/centers/me, настройки
      people.py        — учителя, ученики, родители, связи, инвайты
      lessons.py       — загрузка, список, статус, транскрипт, кадры, анализ
      reports.py       — отчёты (staff)
      parent.py        — /api/parent/* (кабинет родителя, авторизация по magic-токену)
      progress.py      — агрегаты прогресса ученика (staff и parent)
      telegram.py      — webhook-эндпоинт
      health.py        — /api/health
    pipeline/
      runner.py        — оркестратор: run_lesson_pipeline(lesson_id) со стадиями и статусами
      media.py         — ffprobe, извлечение аудио, ключевые кадры, dHash-дедуп
      analysis.py      — сборка промпта (транскрипт + кадры) → провайдер LLM → валидация LessonAnalysis
      reports.py       — LessonAnalysis → три отчёта (JSON + markdown + telegram HTML)
      progress.py      — обновление skill_states по анализу
      jobs.py          — enqueue / claim / complete jobs
    providers/
      stt/base.py      — STTProvider protocol: transcribe(audio_path, language_hints) -> Transcript
      stt/{mock,whisper_local,openai,deepgram,assemblyai,google,elevenlabs,yandex}.py
      stt/registry.py  — get_stt_provider(settings)
      llm/base.py      — LLMProvider protocol: analyze(prompt_parts) -> dict (JSON), generate_text(...)
      llm/{mock,anthropic,openai}.py, llm/registry.py
      messaging/base.py— Messenger protocol: send_parent_report(parent, report) ; send_text(...)
      messaging/{mock,telegram,whatsapp}.py, messaging/registry.py
    bot/
      telegram.py      — aiogram Dispatcher: /start <invite_code>, /progress, /help, привязка chat_id к родителю
    services/
      auth.py          — пароли, JWT, magic-ссылки
      notifications.py — что/кому/когда отправлять, запись в notifications
      storage.py       — пути в MEDIA_ROOT: media/{center_id}/{lesson_id}/{source.ext|audio.wav|frames/NNNN.jpg}
    seed_demo.py       — демо-данные (использует demo/)
    worker.py          — цикл воркера
  tests/
  benchmarks/
    stt_bench.py       — сравнение STT-провайдеров (WER/CER по языкам ru/kk/mixed), синтетический датасет
    README.md          — методика и результаты
frontend/              — React-приложение (§7)
demo/                  — фикстуры: транскрипт демо-урока, кадры, готовый анализ, сценарий урока
docs/                  — CONTRACT.md (этот файл), ARCHITECTURE.md, PROVIDERS.md, LEGAL_KZ.md, DEMO.md
```

## 4. Модель данных (SQLAlchemy, `backend/app/db/models.py`)

Все id — UUID (`uuid4`), `created_at`/`updated_at` с таймзоной. Все бизнес-сущности принадлежат центру (`center_id`) — мультитенантность по колонке.

| Таблица | Поля (ключевые) |
|---|---|
| `centers` | name, city, timezone (default `Asia/Almaty`), default_language (`ru`), settings JSONB |
| `staff_users` | center_id, email (unique), password_hash, full_name, role: `owner` \| `methodist` \| `teacher`, is_active, teacher_id (nullable → teachers) |
| `teachers` | center_id, full_name, subjects (ARRAY text), email, phone, notes |
| `students` | center_id, full_name, grade (класс, int, nullable), school (text), native_language (`kk`), target_subject (`russian`), notes |
| `parents` | center_id, full_name, phone, email, preferred_channel: `telegram` \| `whatsapp` \| `none`, preferred_language `ru` \| `kk`, telegram_chat_id (nullable, unique), invite_code (unique, короткий), whatsapp_phone |
| `parent_students` | parent_id, student_id, relation (мама/папа/опекун) — M:N |
| `lessons` | center_id, teacher_id, student_id, subject, scheduled_at, duration_sec (из ffprobe), source_filename, source_path, media_kind `video`\|`audio`, status (enum §4.1), status_message, consent_confirmed bool (обязательная галочка при загрузке), language_hints ARRAY (`['ru','kk']`), error (text) |
| `transcripts` | lesson_id (unique), provider, language, segments JSONB (list of `{start,end,speaker,text,confidence}`), full_text, speaker_map JSONB (`{"SPEAKER_00":"teacher"}`), word_count, duration_sec |
| `frames` | lesson_id, index, timestamp_sec, path, width, height, phash (text), reason `scene`\|`interval`, selected_for_llm bool |
| `analyses` | lesson_id (unique), provider, model, prompt_version, data JSONB (валидный `LessonAnalysis`), tokens_in, tokens_out, latency_ms |
| `reports` | lesson_id, kind `teacher`\|`manager`\|`parent`, language, data JSONB, markdown text, telegram_html text, version int, published_at |
| `skill_states` | student_id, skill_key (slug), skill_name, status `struggling`\|`developing`\|`mastered`, evidence JSONB (list `{lesson_id, timestamp_sec, note}`), first_seen_lesson_id, last_seen_lesson_id, times_seen, times_struggled, updated_at |
| `jobs` | kind (`process_lesson`), payload JSONB, status `queued`\|`running`\|`done`\|`failed`, attempts, max_attempts, locked_at, locked_by, run_after, error |
| `notifications` | center_id, parent_id, lesson_id, channel, status `pending`\|`sent`\|`failed`\|`skipped`, provider_message_id, error, payload JSONB, sent_at |
| `magic_links` | parent_id, token_hash (unique), expires_at, used_at, purpose `portal` |
| `audit_log` | center_id, actor_type `staff`\|`parent`\|`system`, actor_id, action, entity, entity_id, meta JSONB |

### 4.1 Статусы урока (`lessons.status`)

`uploaded → probing → extracting_audio → transcribing → extracting_frames → analyzing → reporting → notifying → done` и терминальный `failed` (с `error`). Каждая стадия пишет статус в БД до начала работы — фронтенд показывает прогресс по опросу `GET /api/lessons/{id}`.

## 5. Схема анализа `LessonAnalysis` (`backend/app/schemas/analysis.py`)

Единая структура, которую возвращает LLM (structured output / JSON) и которую валидирует pydantic. Все таймкоды — секунды от начала записи (float). Все тексты — на русском.

```text
LessonAnalysis
  schema_version: "1.0"
  subject: str                       # "Русский язык"
  topic: str                         # "Падежные окончания имён существительных"
  lesson_summary: str                # 3–5 предложений
  learning_objectives: list[Objective]   # {text, was_checked: bool, evidence: str|None}
  timeline: list[TeachingEvent]      # хронология значимых событий
     TeachingEvent {start, end, kind: explanation|task|student_answer|student_error|re_explanation|check|visual_material|off_topic|other,
                    speaker: teacher|student|both, summary, quote: str|None, frame_ids: list[int]}
  independent_successes: list[Evidence]   # что ученик сделал сам   Evidence {timestamp, description, quote|None, frame_ids}
  errors: list[StudentError]         # {timestamp, skill_key, skill_name, description, quote|None, frame_ids, repeated_after_explanation: bool, corrected: bool}
  misconceptions: list[Misconception]# {skill_key, description, confidence: 0..1, evidence: list[Evidence], signal_strength: strong|useful|weak}
  skills: list[SkillAssessment]      # {skill_key, skill_name, status: struggling|developing|mastered, evidence: list[Evidence]}
  visual_observations: list[VisualObservation]  # {frame_id, timestamp, description, text_detected: str|None, relevance: high|medium|low, issue: str|None}
  teacher_recommendations: list[TeacherRecommendation]  # {timestamp|None, observation, suggestion, priority: high|medium|low}
  manager_indicators: ManagerIndicators  # {objectives_checked: bool, difficulties_detected: bool, re_explanation_happened: bool,
                                          # needs_methodist_review: bool, review_reason: str|None, teacher_talk_share: float|None,
                                          # student_talk_share: float|None, tasks_total: int, tasks_solved_independently: int, lesson_structure_notes: str}
  parent_report: ParentReportContent # {topic_plain, what_child_did_well: list[str], what_was_hard: list[str], what_to_repeat: list[str],
                                      # home_task_suggestion: str|None, encouragement: str, progress_note: str|None}
  limitations: list[str]             # честные оговорки: качество звука, не видно экрана и т.п.
  language_notes: str|None           # смешение ru/kk, терминология
```

`skill_key` — латинский slug, устойчивый между уроками, например `padezhnye_okonchaniya`, `tsya_tsa`, `udarenie`, `bezudarnye_glasnye`, `rod_sushchestvitelnyh`. Промпт содержит стартовый список ключей для русского языка, но LLM может добавлять новые.

Принцип честности: «тему прошли» ≠ «тему усвоили». Статус `mastered` ставится только при наблюдаемых самостоятельных правильных ответах; `misconceptions.signal_strength=weak` для пауз/заминок. Эти правила зашиты в промпт.

## 6. API (`/api`, JSON)

Аутентификация персонала: `Authorization: Bearer <JWT>` (HS256, `SECRET_KEY`, срок 7 дней, payload `{sub: staff_user_id, center_id, role}`).
Аутентификация родителя: magic-ссылка `PUBLIC_BASE_URL/p/<token>` → `POST /api/parent/auth/exchange {token}` → parent JWT (payload `{sub: parent_id, center_id, role:"parent"}`, 30 дней) → `Authorization: Bearer`.

Staff:

```
POST /api/auth/login {email,password} -> {access_token, user}
GET  /api/auth/me
POST /api/auth/register-center {center_name, city, owner_name, email, password} -> токен (self-service регистрация центра)
GET  /api/centers/me ; PATCH /api/centers/me
GET/POST /api/teachers ; GET/PATCH/DELETE /api/teachers/{id}
GET/POST /api/students ; GET/PATCH/DELETE /api/students/{id} ; GET /api/students/{id}/progress
GET/POST /api/parents ; GET/PATCH/DELETE /api/parents/{id}
POST /api/parents/{id}/link-student {student_id, relation}
POST /api/parents/{id}/magic-link -> {url}          # для отправки вручную
POST /api/parents/{id}/send-invite -> отправляет magic-ссылку и Telegram deep-link через выбранный канал
GET  /api/lessons?student_id&teacher_id&status&page ; GET /api/lessons/{id}
POST /api/lessons (multipart: file, student_id, teacher_id, scheduled_at, subject, consent_confirmed=true, language_hints) -> lesson
POST /api/lessons/{id}/reprocess
GET  /api/lessons/{id}/transcript ; GET /api/lessons/{id}/frames ; GET /api/lessons/{id}/analysis
GET  /api/lessons/{id}/reports -> [teacher, manager, parent]
GET  /api/lessons/{id}/reports/{kind}
POST /api/lessons/{id}/reports/parent/send -> повторная отправка родителям
GET  /api/dashboard -> сводка центра: уроков всего/за неделю, доля с проверкой целей, needs_review count, последние уроки
GET  /api/media/frames/{frame_id} -> jpeg (staff или parent с доступом к ученику)
GET  /api/health
```

Parent:

```
POST /api/parent/auth/exchange {token} -> {access_token, parent, students}
GET  /api/parent/me
GET  /api/parent/students -> [{student, last_lesson_at, lessons_count, skills_summary}]
GET  /api/parent/students/{id}/lessons -> список уроков с parent-отчётом (краткая карточка)
GET  /api/parent/students/{id}/lessons/{lesson_id} -> полный parent-отчёт
GET  /api/parent/students/{id}/progress -> skill_states + динамика по урокам
PATCH /api/parent/me {preferred_channel, preferred_language}
```

Telegram: `POST /api/telegram/webhook/{secret}` (режим webhook). Polling-режим стартует в lifespan FastAPI.

Роли: `owner` — всё; `methodist` — всё кроме удаления центра и управления staff; `teacher` — только свои уроки/ученики (фильтр по `teacher_id`), без manager-отчёта. Родитель видит только своих детей.

Ошибки: `{detail: str}` с корректными HTTP-кодами; 404 вместо 403 при доступе к чужой сущности (не раскрываем существование).

## 7. Frontend (`frontend/`)

Маршруты:

```
/login                         — вход персонала; ссылка на регистрацию центра
/register                      — регистрация центра
/                              — Dashboard центра (сводка + последние уроки + needs review)
/lessons                       — список уроков с фильтрами и статусами (авто-обновление пока идёт обработка)
/lessons/upload                — загрузка записи: файл, ученик, учитель, дата, подтверждение согласия (обязательный чекбокс)
/lessons/:id                   — урок: статус-таймлайн обработки, вкладки «Для учителя» / «Для методиста» / «Для родителя» / «Транскрипт» / «Кадры»
/students, /students/:id       — ученики, прогресс по навыкам (статусы, история)
/teachers, /parents            — справочники, у родителя — кнопки «Создать magic-ссылку», «Отправить приглашение»
/p/:token                      — вход родителя по magic-ссылке (обмен токена → редирект)
/parent                        — кабинет родителя: дети → последние уроки → отчёт; прогресс по навыкам, что повторить дома
/parent/students/:id           — прогресс ребёнка
/parent/students/:id/lessons/:lessonId — отчёт по уроку
```

Интерфейс на русском. Мобильная вёрстка обязательна для родительского кабинета (родители открывают ссылку из Telegram на телефоне). Таймкоды в отчётах кликабельны и показывают соответствующий кадр (если есть).

## 8. Отчёты (из одного анализа)

- **Учителю**: конкретные эпизоды с таймкодами (ошибка → было ли переобъяснение → повторилась ли ошибка), рекомендации с формулировками вопросов ученику, замеченное на экране.
- **Методисту/руководителю**: индикаторы качества (цели проверены / трудности выявлены / требуется разбор), доли речи, задачи решены самостоятельно, краткое резюме, без рейтинга учителя.
- **Родителю**: простым языком: тема, что получилось самостоятельно, где были ошибки, что повторить дома, поддерживающая фраза, ссылка в кабинет. Тональность — уважительная, без оценок учителя и без «диагнозов». Длина для Telegram ≤ 1500 символов, HTML-разметка aiogram-совместимая (`<b>`, `<i>`, без вложенных списков).

## 9. Конвенции

- Код и комментарии — на английском, пользовательские строки — на русском. Коммиты — на английском.
- Любая внешняя интеграция — за протоколом в `providers/*/base.py`, выбор через `registry.py` по настройкам; `mock`-реализация обязательна и используется в тестах/демо.
- Никаких секретов в репозитории. `.env.example` — единственный список переменных.
- Логи — structlog, без персональных данных детей в логах (только id).
- Доступ к медиа — только через API с проверкой тенанта; файлы не лежат в публичном static.
- Тесты не зависят от внешних API (всегда mock-провайдеры).
