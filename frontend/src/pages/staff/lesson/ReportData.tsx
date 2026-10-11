/**
 * Defensive renderer for report `data` produced by another module.
 * Known keys get dedicated rendering; everything else is shown as key/value or raw JSON.
 */
import { useState } from "react";
import { Timecode } from "@/components/Timecode";
import { FrameRefs } from "@/components/FrameThumb";
import type { Frame } from "@/types/api";

type Obj = Record<string, unknown>;

const KEY_LABELS: Record<string, string> = {
  summary: "Резюме",
  lesson_summary: "Резюме урока",
  topic: "Тема",
  subject: "Предмет",
  episodes: "Эпизоды",
  recommendations: "Рекомендации",
  visual_issues: "Замечено на экране",
  misconceptions: "Возможные пробелы в понимании",
  indicators: "Индикаторы",
  counts: "Показатели",
  objectives: "Цели урока",
  learning_objectives: "Цели урока",
  skills: "Навыки",
  limitations: "Оговорки",
  language_notes: "Языковые заметки",
  observation: "Наблюдение",
  suggestion: "Рекомендация",
  priority: "Приоритет",
  timestamp: "Время",
  start: "Начало",
  end: "Конец",
  description: "Описание",
  quote: "Цитата",
  skill_name: "Навык",
  skill_key: "Ключ навыка",
  repeated_after_explanation: "Повторилась после объяснения",
  corrected: "Исправлена",
  re_explained: "Было переобъяснение",
  re_explanation: "Переобъяснение",
  error: "Ошибка",
  issue: "Проблема",
  text_detected: "Текст на кадре",
  relevance: "Релевантность",
  frame_id: "Кадр",
  frame_ids: "Кадры",
  objectives_checked: "Цели проверены",
  difficulties_detected: "Трудности выявлены",
  re_explanation_happened: "Было переобъяснение",
  needs_methodist_review: "Требуется разбор методиста",
  review_reason: "Причина разбора",
  teacher_talk_share: "Доля речи учителя",
  student_talk_share: "Доля речи ученика",
  tasks_total: "Заданий всего",
  tasks_solved_independently: "Решено самостоятельно",
  errors_count: "Ошибок",
  independent_successes_count: "Самостоятельных успехов",
  lesson_structure_notes: "Структура урока",
  confidence: "Уверенность",
  signal_strength: "Сила сигнала",
  status: "Статус",
  evidence: "Подтверждения",
  teacher_name: "Преподаватель",
  student_name: "Ученик",
  date: "Дата",
  version: "Версия",
  talk_shares: "Доли речи",
  key_episodes: "Ключевые эпизоды",
  structure_notes: "Структура урока",
  needs_review: "Требуется разбор",
  student_first_name: "Ученик",
  teacher_share: "Учитель",
  student_share: "Ученик",
  speaker: "Кто говорит",
  summary_text: "Резюме",
  was_checked: "Проверена",
  text: "Текст",
  first_error: "Первая ошибка",
  repeated: "Повторилась",
  objectives_total: "Целей всего",
  objectives_checked_count: "Целей проверено",
  errors_total: "Ошибок всего",
  errors_repeated: "Повторных ошибок",
  errors_corrected: "Исправленных ошибок",
  independent_successes_total: "Самостоятельных успехов",
  misconceptions_total: "Пробелов в понимании",
  visual_issues_total: "Замечаний по экрану",
  skills_struggling: "Навыков с трудностями",
  skills_developing: "Навыков в процессе",
  skills_mastered: "Навыков освоено",
  recommendations_total: "Рекомендаций",
  duration_sec: "Длительность",
  words_total: "Слов в транскрипте",
};

const VALUE_LABELS: Record<string, string> = {
  high: "высокий",
  medium: "средний",
  low: "низкий",
  strong: "сильный",
  useful: "полезный",
  weak: "слабый",
  struggling: "трудности",
  developing: "в процессе",
  mastered: "освоено",
  teacher: "учитель",
  student: "ученик",
  both: "оба",
};

const TIME_KEYS = new Set(["timestamp", "timestamp_sec", "start", "end", "time", "at"]);
// Technical keys that never need to be shown to a person.
const SKIP_KEYS = new Set(["schema_version", "kind", "language", "lesson_id", "student_id", "teacher_id", "center_id", "lesson_date_iso", "date"]);

function label(key: string): string {
  return KEY_LABELS[key] ?? key.replace(/_/g, " ");
}

function isPlainObject(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function Scalar({ k, v }: { k: string; v: unknown }) {
  if (v === null || v === undefined || v === "") return <span className="faint">—</span>;
  if (typeof v === "boolean") return <span>{v ? "да" : "нет"}</span>;
  if (typeof v === "number") {
    if (TIME_KEYS.has(k)) return <Timecode seconds={v} />;
    if (k.endsWith("_share") || k === "confidence") return <span>{Math.round(v * 100)} %</span>;
    return <span>{v}</span>;
  }
  if (typeof v === "string") return <span>{VALUE_LABELS[v] ?? v}</span>;
  return <code>{JSON.stringify(v)}</code>;
}

function ObjectView({ obj, frames }: { obj: Obj; frames?: Frame[] }) {
  const entries = Object.entries(obj).filter(([k]) => !SKIP_KEYS.has(k));
  return (
    <dl className="kv">
      {entries.map(([k, v]) => (
        <ValueRow key={k} k={k} v={v} frames={frames} />
      ))}
    </dl>
  );
}

function ValueRow({ k, v, frames }: { k: string; v: unknown; frames?: Frame[] }) {
  if (k === "frame_ids" && Array.isArray(v)) {
    const ids = v.filter((x): x is number => typeof x === "number");
    if (ids.length === 0) return null;
    return (
      <>
        <dt>{label(k)}</dt>
        <dd>
          {frames ? <FrameRefs frameIds={ids} frames={frames} /> : ids.map((i) => `#${i}`).join(", ")}
        </dd>
      </>
    );
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return null;
    return (
      <>
        <dt>{label(k)}</dt>
        <dd>
          <ArrayView items={v} frames={frames} />
        </dd>
      </>
    );
  }
  if (isPlainObject(v)) {
    return (
      <>
        <dt>{label(k)}</dt>
        <dd>
          <ObjectView obj={v} frames={frames} />
        </dd>
      </>
    );
  }
  return (
    <>
      <dt>{label(k)}</dt>
      <dd>
        <Scalar k={k} v={v} />
      </dd>
    </>
  );
}

function ArrayView({ items, frames }: { items: unknown[]; frames?: Frame[] }) {
  if (items.every((i) => typeof i === "string" || typeof i === "number")) {
    return (
      <ul>
        {items.map((i, idx) => (
          <li key={idx}>{String(i)}</li>
        ))}
      </ul>
    );
  }
  return (
    <div className="stack">
      {items.map((item, idx) => (
        <div key={idx} className="card card-muted" style={{ padding: "10px 14px" }}>
          {isPlainObject(item) ? <ObjectView obj={item} frames={frames} /> : <Scalar k="" v={item} />}
        </div>
      ))}
    </div>
  );
}

/**
 * Render a whole report `data` object: scalar summary first, then arrays/objects as sections.
 * `exclude` hides keys already rendered by a dedicated component.
 */
export function ReportDataView({ data, frames, exclude = [] }: { data: Obj | null | undefined; frames?: Frame[]; exclude?: string[] }) {
  if (!data) return null;
  const skip = new Set([...SKIP_KEYS, ...exclude]);
  const entries = Object.entries(data).filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0));
  if (entries.length === 0) return null;
  const scalars = entries.filter(([, v]) => !Array.isArray(v) && !isPlainObject(v));
  const complex = entries.filter(([, v]) => Array.isArray(v) || isPlainObject(v));
  return (
    <div className="stack">
      {scalars.length > 0 && (
        <dl className="kv">
          {scalars.map(([k, v]) => (
            <ValueRow key={k} k={k} v={v} frames={frames} />
          ))}
        </dl>
      )}
      {complex.map(([k, v]) => (
        <section key={k}>
          <h4>{label(k)}</h4>
          {Array.isArray(v) ? <ArrayView items={v} frames={frames} /> : <ObjectView obj={v as Obj} frames={frames} />}
        </section>
      ))}
    </div>
  );
}

/** Collapsible raw markdown of a report (plain text, pre-wrapped). */
export function ReportMarkdown({ markdown }: { markdown: string | null | undefined }) {
  const [open, setOpen] = useState(false);
  if (!markdown) return null;
  return (
    <div className="mt-4">
      <button type="button" className="link-button small" onClick={() => setOpen((o) => !o)}>
        {open ? "Скрыть текст отчёта" : "Показать текст отчёта (Markdown)"}
      </button>
      {open && <pre className="mt-2 small">{markdown}</pre>}
    </div>
  );
}
