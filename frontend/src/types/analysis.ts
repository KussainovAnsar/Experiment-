/**
 * TypeScript mirror of backend/app/schemas/analysis.py (LessonAnalysis, schema 1.0).
 * Keep in sync with the pydantic models — this is the fixed analysis JSON stored in `analyses.data`.
 */

export type EventKind =
  | "explanation"
  | "task"
  | "student_answer"
  | "student_error"
  | "re_explanation"
  | "check"
  | "visual_material"
  | "off_topic"
  | "other";

export type Speaker = "teacher" | "student" | "both";
export type SkillStatus = "struggling" | "developing" | "mastered";
export type Priority = "high" | "medium" | "low";
export type Relevance = "high" | "medium" | "low";
export type SignalStrength = "strong" | "useful" | "weak";

export interface Objective {
  text: string;
  was_checked: boolean;
  evidence: string | null;
}

export interface Evidence {
  timestamp: number;
  description: string;
  quote: string | null;
  frame_ids: number[];
}

export interface TeachingEvent {
  start: number;
  end: number;
  kind: EventKind;
  speaker: Speaker;
  summary: string;
  quote: string | null;
  frame_ids: number[];
}

export interface StudentError {
  timestamp: number;
  skill_key: string;
  skill_name: string;
  description: string;
  quote: string | null;
  frame_ids: number[];
  repeated_after_explanation: boolean;
  corrected: boolean;
}

export interface Misconception {
  skill_key: string;
  description: string;
  confidence: number;
  signal_strength: SignalStrength;
  evidence: Evidence[];
}

export interface SkillAssessment {
  skill_key: string;
  skill_name: string;
  status: SkillStatus;
  evidence: Evidence[];
}

export interface VisualObservation {
  frame_id: number;
  timestamp: number;
  description: string;
  text_detected: string | null;
  relevance: Relevance;
  issue: string | null;
}

export interface TeacherRecommendation {
  timestamp: number | null;
  observation: string;
  suggestion: string;
  priority: Priority;
}

export interface ManagerIndicators {
  objectives_checked: boolean;
  difficulties_detected: boolean;
  re_explanation_happened: boolean;
  needs_methodist_review: boolean;
  review_reason: string | null;
  teacher_talk_share: number | null;
  student_talk_share: number | null;
  tasks_total: number;
  tasks_solved_independently: number;
  lesson_structure_notes: string;
}

export interface ParentReportContent {
  topic_plain: string;
  what_child_did_well: string[];
  what_was_hard: string[];
  what_to_repeat: string[];
  home_task_suggestion: string | null;
  encouragement: string;
  progress_note: string | null;
}

export interface LessonAnalysis {
  schema_version: "1.0";
  subject: string;
  topic: string;
  lesson_summary: string;
  learning_objectives: Objective[];
  timeline: TeachingEvent[];
  independent_successes: Evidence[];
  errors: StudentError[];
  misconceptions: Misconception[];
  skills: SkillAssessment[];
  visual_observations: VisualObservation[];
  teacher_recommendations: TeacherRecommendation[];
  /** null when served to the `teacher` role (CONTRACT §6: teachers get no manager report). */
  manager_indicators: ManagerIndicators | null;
  parent_report: ParentReportContent;
  limitations: string[];
  language_notes: string | null;
}

/** Russian labels for timeline event kinds. */
export const EVENT_KIND_LABELS: Record<EventKind, string> = {
  explanation: "Объяснение",
  task: "Задание",
  student_answer: "Ответ ученика",
  student_error: "Ошибка ученика",
  re_explanation: "Переобъяснение",
  check: "Проверка",
  visual_material: "Визуальный материал",
  off_topic: "Не по теме",
  other: "Другое",
};

export const SPEAKER_LABELS: Record<Speaker, string> = {
  teacher: "Учитель",
  student: "Ученик",
  both: "Оба",
};

export const SKILL_STATUS_LABELS: Record<SkillStatus, string> = {
  struggling: "Трудности",
  developing: "В процессе",
  mastered: "Освоено",
};

export const PRIORITY_LABELS: Record<Priority, string> = {
  high: "Высокий",
  medium: "Средний",
  low: "Низкий",
};

export const RELEVANCE_LABELS: Record<Relevance, string> = {
  high: "Высокая",
  medium: "Средняя",
  low: "Низкая",
};

export const SIGNAL_STRENGTH_LABELS: Record<SignalStrength, string> = {
  strong: "Сильный сигнал",
  useful: "Полезный сигнал",
  weak: "Слабый сигнал",
};
