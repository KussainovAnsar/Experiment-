# LessonLens — Frontend (`frontend/`)

React 18 + Vite 7 + TypeScript SPA for two audiences:

* **Staff area** (owner / methodist / teacher) — dashboard, lessons, upload, students, teachers, parents.
* **Parent portal** (`/p/:token` → `/parent/**`) — mobile-first, opened from a Telegram magic link.

UI language is Russian; code and comments are English. No UI kit — a small CSS design-token system
(`src/styles/tokens.css`) and hand-written components. Data layer: TanStack Query v5; routing: react-router v6.

## Run

```bash
cd frontend
npm install            # .npmrc sets legacy-peer-deps (works around an npm arborist bug with vitest peers)
npm run dev            # http://localhost:5173, proxies /api → http://localhost:8000
npm run build          # tsc --noEmit (strict) + vite build → dist/
npm run typecheck      # tsc only
npm test               # vitest (unit + mount smoke tests, jsdom)
```

Set `VITE_API_PROXY_TARGET` to proxy `/api` to another backend host during development (see `.env.example`).
In production serve `dist/` from any static host and route `/api/*` (and `/p/*`, `/parent/*`, all other paths
→ `index.html`) to the FastAPI backend; the app only ever uses relative `/api` URLs.

## Structure

```
frontend/
  index.html, vite.config.ts, vitest.config.ts, tsconfig.json, .npmrc
  public/favicon.svg
  src/
    main.tsx                 — QueryClient, BrowserRouter, ToastProvider
    App.tsx                  — route table (CONTRACT §7), auth providers, guards
    api/
      client.ts              — fetch wrapper: Bearer token per realm, ApiError (Russian messages),
                               401 → clears realm + notifies listeners; uploadWithProgress (XHR)
      staff.ts               — typed staff endpoints (auth, center/dashboard, lessons, people, reports)
      parent.ts              — typed parent-portal endpoints (realm "parent")
      queries.ts             — React Query hooks + query keys; lesson polling every 3 s while active
    auth/
      storage.ts             — localStorage keys ll_staff_token / ll_parent_token, withAccessToken(), JWT expiry
      StaffAuth.tsx          — staff context (user, role helpers, logout, 401 → /login)
      ParentAuth.tsx         — parent context (exchange result, 401 → /parent/expired)
      guards.tsx             — RequireStaff, RequireManager, RequireParent, RedirectIfStaff
    components/              — ui.tsx (badges, progress, skeleton, empty/error states, modal…),
                               StatusTimeline, Tabs, Timecode, FrameThumb, TelegramPreview,
                               ParentReportView, SkillsProgress, LessonTable, layouts, Toast
    hooks/useSeek.ts         — seek context (timecode → video.currentTime + nearest frame highlight)
    lib/
      format.ts              — mm:ss, durations, ru-RU dates, percent, pluralize
      status.ts              — LessonStatus labels/tones, pipeline stage names, skill/notification labels
      sanitize.ts            — Telegram HTML sanitiser (allow-list: b i u s code pre a[href] br)
    pages/
      staff/                 — Login, Register, Dashboard, Lessons, Upload, Students, StudentDetail,
                               Teachers, Parents, lesson/ (LessonPage + Teacher/Manager/Parent/Transcript/Frames tabs,
                               ReportData generic renderer)
      parent/                — MagicLink (/p/:token), Expired, Home, Student (lessons | progress), Lesson, Settings
      NotFoundPage.tsx
    styles/tokens.css, global.css
    types/analysis.ts        — TS mirror of backend/app/schemas/analysis.py (LessonAnalysis 1.0)
    types/api.ts             — DTOs of CONTRACT §6
```

## Routes (CONTRACT §7)

| Path | Page | Guard |
|---|---|---|
| `/login`, `/register` | staff login / centre self-registration | redirect to `/` if already logged in |
| `/` | dashboard | staff |
| `/lessons`, `/lessons/upload`, `/lessons/:id` | list (filters, polling), upload, lesson detail | staff |
| `/students`, `/students/:id` | students, skills progress | staff |
| `/teachers`, `/parents` | directories (magic link / invite) | owner, methodist only |
| `/p/:token` | magic-link exchange → `/parent` | — |
| `/parent`, `/parent/settings` | children cards, preferences | parent |
| `/parent/students/:id` (`?view=progress`) | lessons cards / skills progress | parent |
| `/parent/students/:id/lessons/:lessonId` | full parent report | parent |
| `/parent/expired` | friendly "link expired" page | — |

Role awareness: `teacher` role sees no Преподаватели / Родители nav items (routes redirect to `/`) and no
«Для методиста» tab; the lessons filter by teacher is hidden for them (the backend already scopes data).

## Auth & API assumptions

* Staff JWT in `localStorage["ll_staff_token"]`, parent JWT in `localStorage["ll_parent_token"]`
  (plus cached user / parent session for instant render; `/api/auth/me` revalidates on load).
* Every API call sends `Authorization: Bearer <jwt>` of its realm. On **401** the realm is cleared and the
  router redirects: staff → `/login` (with "session expired" notice), parent → `/parent/expired`.
* Errors are `{detail: string | [...]}`; `ApiError.message` is a Russian user-facing text (FastAPI validation
  arrays are reduced to the first message).
* List endpoints return `{items,total,page,page_size}`; `?page=&page_size=` (≤ 100).
* Lesson DTO: `status` ∈ uploaded…done|failed, `stage_index / stage_total` drives the progress bar and the
  status timeline; the lesson query refetches every **3 s** while the status is not `done`/`failed`, and all
  lesson sub-queries are invalidated when it reaches a terminal state. Dashboard and lesson list also poll
  while any lesson is processing.
* Upload: `POST /api/lessons` multipart with fields `file, student_id, teacher_id?, scheduled_at (ISO),
  subject=russian, consent_confirmed=true, language_hints="ru,kk"`, sent via XHR for progress events.
* Reports: `GET /api/lessons/:id/reports` → `[{kind, language, data, markdown, telegram_html, version, created_at}]`;
  the newest version per kind is used. Teacher/manager `data` shapes belong to the reports module, so the tabs
  render primarily from the **analysis JSON** (`GET /api/lessons/:id/analysis`, fixed `LessonAnalysis` schema,
  mirrored in `src/types/analysis.ts`) and additionally render report `data` through a defensive key/value
  renderer (`ReportDataView`) plus the Markdown text. Parent `data` is rendered by `ParentReportView`
  (also used in the parent portal) with optional fields.
* `POST /api/lessons/:id/reports/parent/send` → `Notification[]`; statuses are shown per channel.
  A 503 "Модуль уведомлений недоступен" is surfaced as-is.
* Parent portal: `POST /api/parent/auth/exchange {token}` → `{access_token, parent, students}`; the exchange can
  be repeated until the link expires (parents reopen it from Telegram).
* Progress (`/api/students/:id/progress`, `/api/parent/students/:id/progress`):
  `{skills:[{skill_key, skill_name, status, times_seen, times_struggled, first/last_seen_lesson_id, evidence:[{lesson_id, timestamp_sec, note, status?}]}],
    lessons:[{lesson_id, scheduled_at, topic, errors_count, independent_successes_count, tasks_total, tasks_solved_independently}], counts:{struggling, developing, mastered}}`.
  Extra fields are ignored.

### Media and the `access_token` query parameter

`<img>` and `<video>` tags cannot send an `Authorization` header. Media URLs therefore carry a **short-lived,
lesson-scoped media token** (`typ=media`, ~10 minutes, `MEDIA_TOKEN_TTL_MINUTES`) — never the staff/parent session
JWT, which would end up in reverse-proxy logs and browser history:

* staff: `GET /api/lessons/{id}/media-token` (`useLessonMediaToken`, refreshed every 8 minutes while the lesson
  page is open) → `MediaTokenContext` on the lesson page; `useMediaUrl()` / `withMediaToken(url, token)`
  (`src/auth/media.tsx`, `src/auth/storage.ts`) append `?access_token=…` to frame and source URLs;
* parent: `GET /api/parent/students/{sid}/lessons/{lid}` returns `media_token` next to the (already filtered)
  `frames`; the parent lesson page provides it through the same context.

The backend accepts a media token **only** from the query string of `/api/media/*` and only for the lesson it was
issued for; session tokens are accepted only in the header.
Never use this pattern for other endpoints.

## Telegram preview

`telegram_html` from the parent report is rendered in a chat-bubble preview after
`sanitizeTelegramHtml()` — an allow-list sanitiser (`b strong i em u s code pre a br`), `href` limited to
`http(s)://`, `tg://`, `mailto:`, unknown tags escaped as text, tags auto-balanced, newlines → `<br>`.
A character counter warns above the 1500-character guideline.

## Lesson page interactions

* Status timeline (9 stages with Russian names) on top; failed state shows the error.
* Video player (or audio element for audio-only lessons) with a keyframe strip below it.
* Every timecode in reports/transcript is a button: it seeks the player and highlights the nearest
  keyframe (strip + «Кадры» tab). The transcript highlights the segment under the playhead.
* Teacher tab: episodes built from `analysis.errors` + the next `re_explanation` timeline event
  (ошибка → переобъяснение → повторилась / исправлена), recommendations sorted by priority, visual issues
  with frame thumbnails, misconceptions, independent successes, limitations.
* Manager tab: indicator cards (Цели проверены / Трудности выявлены / Требуется разбор методиста),
  talk-share bar, tasks solved independently, chronology table. No teacher rating anywhere.
* Parent tab: the report as the parent sees it, Telegram preview, «Отправить родителям» with resulting
  notification statuses.

## Tests

`npm test` runs vitest in jsdom:

* `src/lib/format.test.ts` — mm:ss / h:mm:ss, durations, pluralisation;
* `src/auth/storage.test.ts` — token keys, realm isolation, `withAccessToken`, JWT expiry;
* `src/lib/sanitize.test.ts` — Telegram HTML sanitiser (XSS vectors, balancing, hrefs);
* `src/App.smoke.test.tsx` — mounts the app on public routes (login, register, expired, 404, failed exchange).

## Known limitations

* No Playwright e2e in this package (Contract lists it as a repo-level smoke; the app was verified manually
  against the real backend with mock providers via the Vite proxy).
* Markdown report text is shown as pre-wrapped plain text (no Markdown renderer, to stay dependency-free).
* Tables are horizontally scrollable on phones; the staff area is designed for ≥ 360 px but optimised for desktop.
