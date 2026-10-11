/** Lesson status metadata: Russian labels, badge tones, pipeline stage names. */
import type { LessonStatus, NotificationStatus } from "@/types/api";
import type { SkillStatus } from "@/types/analysis";

export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger" | "accent";

export interface StatusMeta {
  label: string;
  tone: BadgeTone;
  /** True while the pipeline is still running (poll the lesson). */
  active: boolean;
}

export const LESSON_STATUS_META: Record<LessonStatus, StatusMeta> = {
  uploaded: { label: "Загружено", tone: "info", active: true },
  probing: { label: "Анализ файла", tone: "info", active: true },
  extracting_audio: { label: "Извлечение аудио", tone: "info", active: true },
  transcribing: { label: "Расшифровка речи", tone: "info", active: true },
  extracting_frames: { label: "Ключевые кадры", tone: "info", active: true },
  analyzing: { label: "Анализ урока", tone: "accent", active: true },
  reporting: { label: "Формирование отчётов", tone: "accent", active: true },
  notifying: { label: "Уведомление родителей", tone: "accent", active: true },
  done: { label: "Готово", tone: "success", active: false },
  failed: { label: "Ошибка", tone: "danger", active: false },
};

/** Pipeline stages in order (CONTRACT §4.1), used for the status timeline. */
export const PIPELINE_STAGES: { status: LessonStatus; label: string }[] = [
  { status: "uploaded", label: "Загружено" },
  { status: "probing", label: "Анализ файла" },
  { status: "extracting_audio", label: "Аудио" },
  { status: "transcribing", label: "Расшифровка" },
  { status: "extracting_frames", label: "Кадры" },
  { status: "analyzing", label: "ИИ-анализ" },
  { status: "reporting", label: "Отчёты" },
  { status: "notifying", label: "Уведомления" },
  { status: "done", label: "Готово" },
];

export function isLessonActive(status: LessonStatus): boolean {
  return LESSON_STATUS_META[status]?.active ?? false;
}

/** Progress 0..1 from stage_index/stage_total (falls back to the stage table). */
export function lessonProgress(stageIndex: number | null, stageTotal: number, status: LessonStatus): number {
  if (status === "done") return 1;
  if (status === "failed") return 0;
  const total = stageTotal > 0 ? stageTotal : PIPELINE_STAGES.length - 1;
  const idx = stageIndex ?? PIPELINE_STAGES.findIndex((s) => s.status === status);
  if (idx < 0) return 0;
  return Math.min(1, Math.max(0, idx / total));
}

export const SKILL_STATUS_META: Record<SkillStatus, { label: string; tone: BadgeTone; hint: string }> = {
  struggling: {
    label: "Трудности",
    tone: "danger",
    hint: "Ребёнок пока ошибается в этой теме — её стоит повторить дома.",
  },
  developing: {
    label: "В процессе",
    tone: "warning",
    hint: "Тема знакома, но ещё нужна практика и подсказки.",
  },
  mastered: {
    label: "Освоено",
    tone: "success",
    hint: "Ребёнок уверенно справляется самостоятельно.",
  },
};

export const NOTIFICATION_STATUS_META: Record<NotificationStatus, { label: string; tone: BadgeTone }> = {
  pending: { label: "В очереди", tone: "info" },
  sent: { label: "Отправлено", tone: "success" },
  failed: { label: "Ошибка отправки", tone: "danger" },
  skipped: { label: "Пропущено", tone: "neutral" },
};

/** Russian text for notification `payload.reason` slugs (services/notifications.py). */
export const NOTIFICATION_REASON_LABELS: Record<string, string> = {
  telegram_not_linked: "Telegram не подключён",
  whatsapp_phone_missing: "Нет номера WhatsApp",
  channel_none: "Уведомления отключены",
  provider_not_configured: "Бот не настроен",
};

export const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  none: "Не отправлять",
};

export const LANGUAGE_LABELS: Record<string, string> = {
  ru: "Русский",
  kk: "Қазақша",
};

export const ROLE_LABELS: Record<string, string> = {
  owner: "Владелец",
  methodist: "Методист",
  teacher: "Преподаватель",
};
