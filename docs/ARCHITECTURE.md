# LessonLens — архитектура

Описание системы на уровне компонентов и потоков данных. Нормативный документ — [`CONTRACT.md`](CONTRACT.md) (при расхождении побеждает контракт); правовые требования к данным — [`LEGAL_KZ.md`](LEGAL_KZ.md); провайдеры STT/LLM — [`PROVIDERS.md`](PROVIDERS.md); мессенджеры — [`MESSAGING.md`](MESSAGING.md); фронтенд — [`FRONTEND.md`](FRONTEND.md).

Содержание: [1. Обзор](#1-обзор-системы) · [2. Компоненты](#2-компоненты-и-их-зоны-ответственности) · [3. Пайплайн](#3-пайплайн-обработки-урока) · [4. Данные и приватность](#4-поток-данных-где-живут-персональные-данные) · [5. Провайдеры](#5-абстракция-провайдеров) · [6. Развёртывание](#6-развёртывание) · [7. Масштабирование](#7-масштабирование-и-надёжность) · [8. Безопасность](#8-безопасность) · [9. Дорожная карта](#9-дорожная-карта)

---

## 1. Обзор системы

LessonLens принимает запись онлайн-урока (Zoom/Meet, видео или аудио), извлекает аудио и ключевые кадры, распознаёт речь с разделением говорящих, отдаёт транскрипт и кадры мультимодальной LLM, получает строго типизированный `LessonAnalysis` и из него собирает три отчёта — преподавателю, методисту/руководителю и родителю. Родитель получает краткий отчёт в Telegram (WhatsApp — заготовка) и ссылку в мобильный кабинет; история навыков ученика (`skill_states`) накапливается от урока к уроку.

```mermaid
flowchart LR
    subgraph Центр["Репетиторский центр (браузер)"]
        U[Сотрудник центра<br/>owner / methodist / teacher]
    end
    subgraph Родитель["Родитель (телефон)"]
        P["Кабинет родителя<br/>/p/:token → /parent"]
        TG[Telegram-бот]
    end

    U -- "POST /api/lessons (файл + consent_confirmed)" --> API
    P -- "magic-ссылка → parent JWT" --> API
    TG <-- "aiogram polling / webhook" --> BOT

    subgraph Backend["Backend (FastAPI, Python 3.13)"]
        API[API<br/>app/api/*]
        BOT[Бот<br/>app/bot/telegram.py]
        JOBS[(jobs<br/>PostgreSQL-очередь)]
        W[Worker<br/>python -m app.worker]
        RUN[Pipeline runner<br/>app/pipeline/runner.py]
    end

    API -- "enqueue process_lesson" --> JOBS
    JOBS -- "FOR UPDATE SKIP LOCKED" --> W
    W --> RUN
    API -. "PIPELINE_MODE=inline:<br/>фоновая задача" .-> RUN

    subgraph Pipeline["Стадии пайплайна"]
        FF[ffprobe / ffmpeg<br/>audio.wav 16 kHz mono]
        STT[STT-провайдер<br/>транскрипт + говорящие]
        KF[Ключевые кадры<br/>scene + interval, dHash]
        LLM[Мультимодальный LLM<br/>→ LessonAnalysis JSON]
        REP[Отчёты ×3<br/>teacher / manager / parent]
        PRG[Прогресс<br/>skill_states]
        NTF[Уведомления<br/>Telegram / WhatsApp]
    end

    RUN --> FF --> STT --> KF --> LLM --> REP --> PRG --> NTF
    NTF --> TG
    NTF -. "magic-ссылка" .-> P

    subgraph Storage["Хранилища (территория РК)"]
        PG[(PostgreSQL 16<br/>все таблицы)]
        MR[(MEDIA_ROOT<br/>source / audio.wav / frames)]
    end

    API <--> PG
    RUN <--> PG
    RUN <--> MR
    API -- "GET /api/media/frames/{id}<br/>проверка тенанта" --> MR

    subgraph Ext["Внешние провайдеры (за пределами РК)"]
        X1[Anthropic / OpenAI<br/>LLM]
        X2[Deepgram / OpenAI / AssemblyAI /<br/>Google / ElevenLabs / Yandex<br/>STT]
        X3[Telegram / Meta WhatsApp]
    end
    STT -. "только audio.wav" .-> X2
    LLM -. "транскрипт без ФИО + кадры" .-> X1
    NTF -.-> X3
```

Ключевые решения:

| Решение | Почему |
|---|---|
| Очередь — таблица `jobs` в PostgreSQL, а не Redis/RabbitMQ | Один инфраструктурный компонент меньше; `FOR UPDATE SKIP LOCKED` даёт безопасный конкурентный захват задач; объёмы пилота — десятки уроков в день |
| Два режима запуска пайплайна (`inline` / `worker`) | `inline` — демо и разработка на одном процессе; `worker` — продакшен, API не блокируется долгой обработкой |
| Все внешние интеграции — за протоколами с обязательной `mock`-реализацией | Тесты и демо не зависят от ключей и сети; смена провайдера — переменная окружения; юридические ограничения (локализация, трансграничная передача) решаются выбором провайдера, а не переписыванием кода |
| Один `LessonAnalysis` → три отчёта | LLM вызывается один раз на урок; отчёты детерминированно рендерятся из JSON, их можно перегенерировать без повторного анализа |
| Медиа только через API | Проверка тенанта и роли на каждом файле; файлы не лежат в публичном static |
| Мультитенантность по колонке `center_id` | Простая модель для MVP; 404 вместо 403 при доступе к чужой сущности |

---

## 2. Компоненты и их зоны ответственности

Пути — относительно `backend/app/` (структура из `CONTRACT.md` §3).

| Компонент | Путь | Ответственность | Ключевые функции |
|---|---|---|---|
| Приложение | `main.py` | Фабрика FastAPI: роутеры, CORS, request-id, JSON-логи, обработка ошибок, lifespan (старт Telegram-polling при заданном токене) | `create_app()` |
| Настройки | `config.py` | `Settings` (pydantic-settings), читает `.env` из корня и `backend/` | `get_settings()` |
| Логи | `logging.py` | structlog, JSON, без ПД (только id) | `configure_logging()`, `get_logger()` |
| БД | `db/base.py`, `db/session.py`, `db/models.py` | DeclarativeBase с naming convention; async engine/sessionmaker, dependency `get_session`; **все** ORM-модели | `get_engine()`, `get_sessionmaker()`, `get_session()` |
| Миграции | `migrations/` | Alembic (sync psycopg URL в `env.py`) | `alembic upgrade head` |
| Схемы | `schemas/analysis.py`, `schemas/api.py` | `LessonAnalysis` (строгая pydantic-модель, `extra=forbid`); DTO API | — |
| Auth-зависимости | `api/deps.py` | `current_staff`, `current_parent`, `require_role`, принципал для медиа | — |
| API: персонал | `api/auth.py`, `api/centers.py`, `api/people.py`, `api/lessons.py`, `api/reports.py`, `api/progress.py`, `api/media.py`, `api/health.py` | Эндпоинты `CONTRACT.md` §6: логин/регистрация центра, справочники, загрузка урока (multipart, `consent_confirmed` обязателен), статус, транскрипт, кадры, анализ, отчёты, дашборд, медиа с проверкой тенанта | — |
| API: родитель | `api/parent.py` | Обмен magic-токена на parent JWT, дети, уроки, отчёты, прогресс, настройки канала/языка | — |
| API: Telegram | `api/telegram.py` | Webhook `POST /api/telegram/webhook/{secret}` | — |
| Бот | `bot/telegram.py` | aiogram 3 Dispatcher: `/start <invite_code>` (привязка `telegram_chat_id`), `/progress`, `/help`; polling или webhook | `start_polling_background()`, `stop_polling_background()` |
| Оркестратор | `pipeline/runner.py` | Стадии и статусы урока; каждая стадия пишет статус до начала работы; ошибки → `failed` + `error` | `enqueue_lesson_processing(session, lesson_id)`, `run_lesson_pipeline(lesson_id)`, `run_lesson_pipeline_result(...)` |
| Медиа | `pipeline/media.py` | ffprobe, извлечение аудио, кадры по смене сцены + интервалу, dHash-дедупликация, равномерный отбор ≤ `KEYFRAME_MAX` | `probe_media()`, `extract_audio()`, `extract_keyframes()`, `dhash()` |
| Анализ | `pipeline/analysis.py`, `pipeline/prompts/*` | Сборка промпта (транскрипт с таймкодами и ролями, подписи кадров, класс, родной язык, прошлые `skill_states`), вызов LLM, валидация `LessonAnalysis`, запись `analyses` | `build_prompt()`, `select_frames()`, `analyze_lesson()`, `upsert_analysis()` |
| Отчёты | `pipeline/reports.py` | `LessonAnalysis` → JSON + markdown (+ Telegram HTML для родителя), версии отчётов | `build_reports()`, `build_report_payloads()`, `render_parent_report_telegram_html()` |
| Прогресс | `pipeline/progress.py` | Обновление `skill_states` по анализу (честные статусы: `mastered` только при наблюдаемых самостоятельных ответах), сводка прогресса | `update_progress()`, `student_progress_summary()` |
| Очередь | `pipeline/jobs.py` | enqueue / claim (`SKIP LOCKED`) / complete / fail с экспоненциальным backoff / освобождение зависших | `enqueue()`, `claim_next()`, `complete()`, `fail()`, `release_stale()` |
| Воркер | `worker.py` | Цикл: опрос каждые 2 с, один job за раз, graceful shutdown по SIGTERM, освобождение зависших задач каждые 5 мин | `python -m app.worker` |
| Провайдеры | `providers/stt/*`, `providers/llm/*`, `providers/messaging/*` | Протоколы + реализации + `registry.py` (выбор по настройкам) | `get_stt_provider()`, `get_llm_provider()`, `get_messenger()` |
| Сервисы | `services/auth.py`, `services/notifications.py`, `services/storage.py`, `services/audit.py` | Пароли (bcrypt), JWT (HS256), magic-ссылки (хеш токена в БД); что/кому/когда отправлять и запись в `notifications`; раскладка файлов в `MEDIA_ROOT`; журнал действий | `create_staff_token()`, `create_magic_link()`, `exchange_magic_link()`, `notify_parents_for_lesson()`, `send_parent_invite()`, `lesson_dir()`, `is_within_media_root()`, `audit()` |
| Демо | `seed_demo.py`, `../../demo/` | Демо-центр, учитель, ученик, родитель, один обработанный урок на синтетических фикстурах | `python -m app.seed_demo` |
| Фронтенд | `../../frontend/` | React 18 + Vite + TS: кабинет центра и мобильный кабинет родителя, опрос статуса урока каждые 3 с | см. `FRONTEND.md` |

---

## 3. Пайплайн обработки урока

Статусы (`lessons.status`, `CONTRACT.md` §4.1): `uploaded → probing → extracting_audio → transcribing → extracting_frames → analyzing → reporting → notifying → done`, терминальный `failed`.

```mermaid
sequenceDiagram
    autonumber
    participant S as Сотрудник центра
    participant API as API (api/lessons.py)
    participant DB as PostgreSQL
    participant Q as jobs (очередь)
    participant W as Worker / inline task
    participant R as runner.py
    participant M as media.py (ffmpeg)
    participant STT as STT-провайдер
    participant LLM as LLM-провайдер
    participant N as notifications.py
    participant TG as Telegram

    S->>API: POST /api/lessons (файл, student_id, teacher_id, consent_confirmed=true)
    API->>API: проверка роли и тенанта, consent обязателен
    API->>DB: lessons(status=uploaded), файл → MEDIA_ROOT/{center}/{lesson}/source.ext
    API->>Q: enqueue process_lesson {lesson_id}
    API-->>S: 201 lesson (фронтенд опрашивает GET /api/lessons/{id})

    W->>Q: claim_next (FOR UPDATE SKIP LOCKED)
    W->>R: run_lesson_pipeline_result(lesson_id)
    R->>DB: status=probing
    R->>M: probe_media → duration, fps, media_kind
    R->>DB: status=extracting_audio
    R->>M: extract_audio → audio.wav (16 kHz mono)
    R->>DB: status=transcribing
    R->>STT: transcribe(audio.wav, ['ru','kk'])
    STT-->>R: segments {start,end,speaker,text,confidence}
    R->>DB: transcripts (speaker_map teacher/student)
    R->>DB: status=extracting_frames
    R->>M: extract_keyframes (scene + interval, dHash, ≤ KEYFRAME_MAX)
    R->>DB: frames (path, timestamp, phash, selected_for_llm)
    R->>DB: status=analyzing
    R->>LLM: analyze(system + транскрипт без ФИО + ≤ LLM_MAX_FRAMES кадров)
    LLM-->>R: JSON → валидация LessonAnalysis
    R->>DB: analyses (data, tokens, latency)
    R->>DB: status=reporting
    R->>DB: reports ×3 (teacher, manager, parent) + skill_states
    R->>DB: status=notifying
    R->>N: notify_parents_for_lesson
    N->>DB: magic_links (хеш токена, TTL 72 ч), notifications
    N->>TG: HTML ≤ 1500 символов + кнопка «Открыть кабинет»
    R->>DB: status=done
    W->>Q: complete / fail (retry с backoff до max_attempts)
```

Поведение при сбоях: исключение на любой стадии → `status=failed`, понятное русское `status_message` и техническое `error` (пути файловой системы вырезаются). Job перезапускается с backoff 30 с → 60 с → 120 с … (до `max_attempts=3`) **только при временных ошибках** (STT/LLM/сеть); детерминированные сбои — битый файл (`MediaError`), невалидный ответ модели (`ValidationError`), ошибка БД, отсутствующий модуль — завершают job сразу. Запуск начинается с атомарного захвата урока (`pipeline/locking.claim_lesson`, compare-and-set по `lessons.status`): второй параллельный запуск (двойной клик, дубликат job'а) ничего не делает; в режиме worker дубликаты queued/running job'ов для урока не создаются. `POST /api/lessons/{id}/reprocess` отвечает 409, пока урок обрабатывается, но принимает «брошенный» урок, чей промежуточный статус не менялся дольше `PIPELINE_STALE_MINUTES` (30). В режиме inline API при старте помечает такие уроки `failed` («Обработка прервана перезапуском сервера»), а при остановке ждёт фоновые задачи до 10 с и отменяет остальные. Стадии идемпотентны (транскрипт/анализ перезаписываются, кадры очищаются перед повторным извлечением, отчёты получают новую `version`, `skill_states` пересчитываются — навык, которого нет в новом анализе, теряет свидетельства этого урока). Недоступность мессенджера не валит урок — уведомление записывается как `failed`/`skipped` в `notifications`.

Инвариант честности анализа зашит в системный промпт (`pipeline/prompts/system_prompt.py`): «тему прошли» ≠ «тему усвоили»; `mastered` только при наблюдаемых самостоятельных правильных ответах; паузы и заминки — `signal_strength=weak`; обязательные оговорки в `limitations`.

---

## 4. Поток данных: где живут персональные данные

Таблица — основа для раздела согласия о получателях ([`CONSENT_TEMPLATE.md`](CONSENT_TEMPLATE.md) §1.4) и для retention-политики ([`LEGAL_KZ.md`](LEGAL_KZ.md) §2.5).

| Данные | Где хранится | Кто видит | Покидает РК? | Срок (целевая политика) |
|---|---|---|---|---|
| Исходная запись `source.ext` (лицо, голос, речь ребёнка и учителя) | `MEDIA_ROOT/{center_id}/{lesson_id}/` на сервере в РК | Персонал центра через `GET /api/media/lessons/{id}/source` (проверка тенанта/роли) | **Нет** | 30 дней после `done` (retention job — в бэклоге) |
| `audio.wav` | там же | Никто через API (внутренний артефакт) | **Да → STT-провайдер** (если не `whisper_local`); у провайдера — по его политике (ZDR/opt-out) | удаление после транскрипции |
| Кадры `frames/NNNN.jpg` (экран, доска, иногда лица) | там же; метаданные в `frames` | Персонал центра; родитель — только кадры уроков своего ребёнка | **Да → LLM-провайдер** (≤ `LLM_MAX_FRAMES`) | ≤ 12 месяцев |
| Транскрипт (`transcripts.segments`, `full_text`) | PostgreSQL в РК | Персонал центра | **Да → LLM-провайдер** (с ролями, **без ФИО**) | срок договора + 6 мес. |
| Анализ (`analyses.data` = `LessonAnalysis`) | PostgreSQL | Персонал центра | Нет (возвращается от LLM и хранится у нас) | срок договора + 6 мес. |
| Отчёты (`reports`) | PostgreSQL | teacher — свой; manager — owner/methodist; parent — персонал и родитель | Родительский отчёт (≤ 1500 символов, имя ребёнка) **→ Telegram/WhatsApp** по выбору родителя | срок договора + 6 мес. |
| `skill_states` (навыки, evidence с таймкодами) | PostgreSQL | Персонал центра, родитель — по своему ребёнку | Нет | срок договора + 6 мес. |
| ФИО, класс, школа, родной язык ученика | `students` | Персонал центра (teacher — только свои), родитель | Класс и родной язык → LLM (педагогический контекст); ФИО — нет | до удаления по запросу / окончания договора |
| ФИО, телефон, e-mail, `telegram_chat_id`, `invite_code` родителя | `parents` | Персонал центра, сам родитель | `chat_id` используется для отправки в Telegram | до отзыва согласия |
| ФИО, контакты учителя | `teachers`, `staff_users` | Персонал центра | Нет (ФИО не в промпте) | срок договора |
| Пароли персонала | `staff_users.password_hash` (bcrypt) | Никто | Нет | — |
| Magic-токены | `magic_links.token_hash` (SHA-256), TTL 72 ч | Никто (сравнение по хешу) | Ссылка отправляется в Telegram | до `expires_at` |
| JWT | только у клиента (localStorage по realm) | — | — | staff 7 дней, parent 30 дней |
| Журнал действий `audit_log` | PostgreSQL | owner | Нет; `meta` без ПД | 3 года |
| Логи приложения (structlog JSON) | stdout → сборщик логов | DevOps | Нет; только id, request-id, статусы | 30–90 дней |
| Уведомления `notifications` | PostgreSQL | Персонал центра | Нет (payload без текста отчёта и без magic-ссылки, кроме недоставленных приглашений) | 3 года |

Принципы: (1) исходная база — всегда в РК (требование локализации); (2) вовне уходит минимум: аудио — только в STT, транскрипт и кадры — только в LLM, ФИО — никуда; (3) всё, что вернулось от провайдеров, хранится у нас, а не у них; (4) доступ к файлам — только через API с проверкой `center_id` и роли/связи родитель–ученик; (5) в логах — только идентификаторы.

---

## 5. Абстракция провайдеров

Каждая интеграция — протокол в `providers/*/base.py`, реализации рядом, выбор в `registry.py` по `Settings`. `mock` обязателен и используется в тестах и демо (`CONTRACT.md` §9).

```mermaid
classDiagram
    class STTProvider {
        <<protocol>>
        +transcribe(audio_path, language_hints) Transcript
    }
    class LLMProvider {
        <<protocol>>
        +analyze(prompt_parts) dict
        +generate_text(...) str
    }
    class Messenger {
        <<protocol>>
        +send_parent_report(parent, report)
        +send_text(...)
    }
    STTProvider <|.. MockSTT
    STTProvider <|.. WhisperLocal
    STTProvider <|.. Deepgram
    STTProvider <|.. OpenAISTT
    STTProvider <|.. AssemblyAI
    STTProvider <|.. GoogleSTT
    STTProvider <|.. ElevenLabs
    STTProvider <|.. YandexSTT
    LLMProvider <|.. MockLLM
    LLMProvider <|.. AnthropicLLM
    LLMProvider <|.. OpenAILLM
    Messenger <|.. MockMessenger
    Messenger <|.. TelegramMessenger
    Messenger <|.. WhatsAppMessenger
    class Registry {
        +get_stt_provider(settings)
        +get_llm_provider(settings)
        +get_messenger(settings, channel)
    }
    Registry --> STTProvider
    Registry --> LLMProvider
    Registry --> Messenger
```

| Слой | Переменная | Значения | Замечания |
|---|---|---|---|
| STT | `STT_PROVIDER` | `mock` \| `whisper_local` \| `openai` \| `deepgram` \| `assemblyai` \| `google` \| `elevenlabs` \| `yandex` | `whisper_local` (faster-whisper, `WHISPER_MODEL`) не передаёт аудио за пределы сервера; облачные — трансграничная передача аудио. Транскрипт нормализуется к `{start,end,speaker,text,confidence}`; диаризация → `speaker_map` (`SPEAKER_00 → teacher` по доле речи/эвристике) |
| LLM | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_MAX_FRAMES` | `mock` \| `anthropic` (по умолчанию `claude-sonnet-5-5`, изображения как base64 image blocks, structured output через JSON) \| `openai` | Ответ валидируется `LessonAnalysis`; невалидный JSON → ошибка валидации в логе (без содержимого), стадия `analyzing` → `failed`, job перезапускается с backoff |
| Мессенджеры | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_MODE`, `WHATSAPP_*` | `mock` (без токена) \| `telegram` (polling/webhook) \| `whatsapp` (Meta Cloud API, интерфейс) | Канал выбирается по `parents.preferred_channel`; `mock` пишет в лог/БД |

Добавление нового провайдера: реализовать протокол в `providers/<kind>/<name>.py`, зарегистрировать в `registry.py`, добавить переменные в `.env.example` и `config.py`, описать в `PROVIDERS.md`/`MESSAGING.md` и — для провайдеров за пределами РК — в таблицу получателей согласия.

---

## 6. Развёртывание

### 6.1 Локальная разработка

См. `CONTRACT.md` §2: Postgres `127.0.0.1:5432`, `backend/.venv`, `alembic upgrade head`, `python -m app.seed_demo`, `uvicorn app.main:app --reload --port 8000`, `cd frontend && npm run dev` (5173, прокси `/api` → 8000). `PIPELINE_MODE=inline`, провайдеры `mock`.

### 6.2 Пилот: один сервер в РК, Docker Compose

```mermaid
flowchart TB
    subgraph VM["VM в ЦОД на территории РК (зашифрованный диск)"]
        direction TB
        CADDY["reverse-proxy (Caddy/nginx)<br/>TLS, /api → api, /* → frontend"]
        FE["frontend<br/>nginx + dist/"]
        APIc["api<br/>uvicorn app.main:app"]
        WK["worker ×N<br/>python -m app.worker"]
        PGc[("postgres:16<br/>volume pgdata")]
        MEDIA[("volume media<br/>MEDIA_ROOT")]
        BK["backup<br/>pg_dump + rsync медиа → хранилище в РК"]
    end
    Internet((Интернет)) --> CADDY
    CADDY --> FE
    CADDY --> APIc
    APIc <--> PGc
    WK <--> PGc
    APIc <--> MEDIA
    WK <--> MEDIA
    PGc --> BK
    MEDIA --> BK
    WK -. "HTTPS" .-> EXT["STT / LLM / Telegram API"]
    APIc -. "HTTPS (polling)" .-> EXT
```

Набросок `docker-compose.yml` (файл не входит в MVP; значения — из `.env.example`):

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: lessonlens
      POSTGRES_USER: lessonlens
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U lessonlens"], interval: 5s, retries: 10 }

  api:
    build: ./backend                      # python:3.13-slim + ffmpeg
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://lessonlens:${POSTGRES_PASSWORD}@postgres:5432/lessonlens
      DATABASE_URL_SYNC: postgresql+psycopg://lessonlens:${POSTGRES_PASSWORD}@postgres:5432/lessonlens
      PIPELINE_MODE: worker
      MEDIA_ROOT: /data/media
      APP_ENV: prod
    volumes: ["media:/data/media"]
    depends_on: { postgres: { condition: service_healthy } }

  worker:
    build: ./backend
    command: python -m app.worker
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://lessonlens:${POSTGRES_PASSWORD}@postgres:5432/lessonlens
      PIPELINE_MODE: worker
      MEDIA_ROOT: /data/media
      WORKER_ID: worker-1
    volumes: ["media:/data/media"]
    deploy: { replicas: 2 }
    depends_on: { postgres: { condition: service_healthy } }

  frontend:
    build: ./frontend                     # node:22 build → nginx:alpine, dist/ + SPA fallback
    depends_on: [api]

  proxy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes: ["./deploy/Caddyfile:/etc/caddy/Caddyfile", "caddy_data:/data"]
    depends_on: [api, frontend]

volumes: { pgdata: {}, media: {}, caddy_data: {} }
```

Переменные окружения (единственный источник — `.env.example`): `DATABASE_URL`, `DATABASE_URL_SYNC`, `APP_ENV`, `SECRET_KEY` (≥ 32 байт, ротация инвалидирует все JWT), `PUBLIC_BASE_URL` (адрес фронтенда для magic-ссылок), `API_BASE_URL`, `MEDIA_ROOT`, `CORS_ORIGINS`, `PIPELINE_MODE`, `MAX_UPLOAD_MB`, `KEYFRAME_INTERVAL_SEC`, `KEYFRAME_SCENE_THRESHOLD`, `KEYFRAME_MAX`, `STT_PROVIDER`, `WHISPER_MODEL`, ключи провайдеров (`OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, `ELEVENLABS_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `ANTHROPIC_API_KEY`), `LLM_PROVIDER`, `LLM_MODEL`, `LLM_MAX_FRAMES`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_MODE`, `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_WEBHOOK_SECRET`, `WHATSAPP_ENABLED`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`. Воркер дополнительно читает `WORKER_POLL_INTERVAL_SEC`, `WORKER_ID`, `WORKER_STALE_JOB_HOURS`.

Эксплуатационные требования пилота: TLS на прокси; `MAX_UPLOAD_MB` и таймауты прокси согласованы (записи до 2 ГБ); полный диск под `media` (≈ 1–2 ГБ на час видео до retention); ежедневный `pg_dump` и копия медиа в хранилище **в РК**; `GET /api/health` для мониторинга; алерт на `lessons.status=failed` и на `jobs.status=failed` с `attempts=max_attempts`.

---

## 7. Масштабирование и надёжность

- **Воркеры масштабируются горизонтально** без координации: каждый вызывает `claim_next()` с `SELECT … FOR UPDATE SKIP LOCKED`, поэтому один job достаётся ровно одному воркеру; увеличение `replicas` — единственное действие. Один job = один урок = одна долгая цепочка (ffmpeg → STT → LLM), поэтому пропускная способность ≈ число воркеров / длительность обработки (при облачных STT/LLM — 2–5 минут на часовой урок; `whisper_local` на CPU — RTF 0,5–2, см. `PROVIDERS.md`).
- **Зависшие задачи**: `release_stale()` возвращает в очередь jobs со статусом `running` старше `WORKER_STALE_JOB_HOURS` (воркер упал); стадии идемпотентны, поэтому повтор безопасен.
- **Backpressure**: при лавине загрузок очередь растёт в Postgres, API остаётся отзывчивым; фронтенд показывает `uploaded`/очередь. При необходимости — приоритет (`run_after`) и отдельные воркеры для `kinds`.
- **Медиа**: в MVP — локальный диск (`MEDIA_ROOT`), общий volume между api и worker. Следующий шаг — S3-совместимое хранилище у провайдера в РК (`services/storage.py` — единственное место, знающее о раскладке путей; замена на S3-клиент с подписанными URL за API не меняет остальной код). Lifecycle-правила S3 заменяют часть retention-логики.
- **База данных**: все горячие запросы индексированы по `center_id`, `student_id`, `lesson_id`, `status`; JSONB (`segments`, `data`) читается целиком по уроку. При росте — реплика для чтения дашбордов; партиционирование `audit_log`/`notifications` по месяцам.
- **Внешние провайдеры**: таймауты и повтор на уровне адаптера; ошибки провайдера → `failed` с понятным сообщением и retry job'а; лимиты (rate limit) — через число воркеров.
- **Наблюдаемость**: structlog JSON с `request_id`, `lesson_id`, `job_id`, `stage`, длительностями стадий (`_timed`) и `tokens_in/out`, `latency_ms` в `analyses`; без ПД.
- **Многорегиональность** не планируется: все данные — в РК; разные центры — тенанты одной инсталляции (или отдельные инсталляции по договору).

---

## 8. Безопасность

| Область | Реализация в MVP | Следующий шаг (см. `LEGAL_KZ.md` §9) |
|---|---|---|
| Аутентификация | Staff: email + bcrypt, JWT HS256 (7 дней, `{sub, center_id, role}`); родитель: magic-ссылка (токен 72 ч, в БД — SHA-256) → JWT 30 дней | Ротация `SECRET_KEY`, 2FA для owner |
| Авторизация | Роли owner/methodist/teacher; teacher — только свои уроки/ученики; родитель — только свои дети; 404 вместо 403 | — |
| Тенантность | `center_id` на всех сущностях, проверка в каждом эндпоинте и для медиа | Row-level security в Postgres как второй барьер |
| Медиа | Только через API, защита от подмены путей (`is_within_media_root`), файлы вне static | Шифрование диска/SSE, подписанные URL с коротким TTL |
| Секреты | Только окружение, `.env.example` без значений | Секрет-менеджер провайдера |
| Логи | Без ПД детей — только id | Централизованный сбор с ограниченным доступом |
| Журнал | `audit_log` (actor, action, entity, meta без ПД) | Экспорт журнала для центра |
| Загрузка | `MAX_UPLOAD_MB`, проверка расширения, UUID-имена файлов, `consent_confirmed` обязателен | Антивирус/проверка контейнера ffprobe до сохранения |
| Минимизация | ФИО не в промпте; только аудио в STT; ≤ `LLM_MAX_FRAMES` кадров | Фильтр имён в транскрипте; выбор кадров «экран, не лица» |
| Удаление | — | Retention job, `DELETE /api/students/{id}/data`, экспорт |

---

## 9. Дорожная карта

Согласована с `CONTRACT.md` §1 (v1 — «Post-Lesson Intelligence»; вне MVP: авто-подключение бота к Zoom/Meet, realtime-подсказки, рейтинги преподавателей, биллинг).

```mermaid
timeline
    title LessonLens — версии
    v1 Post-Lesson Intelligence (MVP, пилот) : Загрузка записи → ffmpeg → STT → кадры → мультимодальный LLM : Три отчёта из одного LessonAnalysis : Telegram родителю, мобильный кабинет, история навыков : Mock-провайдеры, очередь в Postgres, один сервер в РК
    v1.x Пилотные доработки : Retention job и удаление по запросу, DPA и согласия : S3 в РК, шифрование at rest : Фильтр имён в транскрипте, локальные STT/LLM как опция : Метрики качества анализа на реальных уроках, калибровка промпта
    v2 Live Copilot (Zoom RTMS) : Подключение к Zoom Realtime Media Streams вместо загрузки файла : Потоковый STT и инкрементальный анализ во время урока : Подсказки преподавателю в реальном времени (ненавязчиво, после урока — полный отчёт) : Автозапись и авто-старт пайплайна по окончании встречи
    v3 Learning Intelligence : Межурочная модель ученика — траектории навыков, прогноз трудностей, персональные домашние задания : Методическая аналитика центра — типовые ошибки по темам, эффективность объяснений (без рейтингов учителей) : Интеграции с LMS и расписанием, WhatsApp, групповые уроки : Самостоятельная настройка рубрик анализа центром
```

Архитектурные заделы под v2/v3 уже в MVP: протоколы провайдеров (потоковый STT — новая реализация `STTProvider` с тем же форматом сегментов); `LessonAnalysis` версионируется (`schema_version`, `prompt_version`) — инкрементальный анализ сможет писать частичные версии; `skill_states` с evidence по урокам — основа межурочной модели; очередь `jobs` с `kind` — для новых типов задач (retention, live-сессии, пересчёт прогресса); мультитенантность по `center_id` — для рубрик и настроек на уровне центра (`centers.settings` JSONB).
