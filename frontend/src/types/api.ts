/**
 * API DTOs (docs/CONTRACT.md §6). All ids are UUID strings, all dates ISO 8601 with timezone.
 */
import type { LessonAnalysis, SkillStatus } from "./analysis";

export type StaffRole = "owner" | "methodist" | "teacher";
export type Channel = "telegram" | "whatsapp" | "none";
export type Language = "ru" | "kk";
export type MediaKind = "video" | "audio";
export type ReportKind = "teacher" | "manager" | "parent";
export type FrameReason = "scene" | "interval";
export type NotificationStatus = "pending" | "sent" | "failed" | "skipped";

export type LessonStatus =
  | "uploaded"
  | "probing"
  | "extracting_audio"
  | "transcribing"
  | "extracting_frames"
  | "analyzing"
  | "reporting"
  | "notifying"
  | "done"
  | "failed";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiErrorBody {
  detail?: string | unknown;
}

// ---------------------------------------------------------------- auth / staff

export interface StaffUser {
  id: string;
  email: string;
  full_name: string;
  role: StaffRole;
  center_id: string;
  center_name: string;
  teacher_id: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type?: string;
  user: StaffUser;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterCenterRequest {
  center_name: string;
  city?: string | null;
  owner_name: string;
  email: string;
  password: string;
}

// ---------------------------------------------------------------- center

export interface Center {
  id: string;
  name: string;
  city: string | null;
  timezone: string;
  default_language: Language;
  settings: Record<string, unknown>;
  created_at: string;
}

// ---------------------------------------------------------------- people

export interface Teacher {
  id: string;
  center_id: string;
  full_name: string;
  subjects: string[];
  email: string | null;
  phone: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
}

export interface TeacherCreate {
  full_name: string;
  subjects?: string[];
  email?: string | null;
  phone?: string | null;
  notes?: string | null;
}

export interface Student {
  id: string;
  center_id: string;
  full_name: string;
  grade: number | null;
  school: string | null;
  native_language: Language;
  target_subject: string;
  notes: string | null;
  is_active: boolean;
  created_at: string;
}

export interface StudentCreate {
  full_name: string;
  grade?: number | null;
  school?: string | null;
  native_language?: Language;
  target_subject?: string;
  notes?: string | null;
}

export interface LinkedStudent {
  id: string;
  full_name: string;
  grade: number | null;
  relation: string | null;
}

export interface Parent {
  id: string;
  center_id: string;
  full_name: string;
  phone: string | null;
  email: string | null;
  preferred_channel: Channel;
  preferred_language: Language;
  telegram_chat_id: number | null;
  telegram_linked: boolean;
  invite_code: string;
  whatsapp_phone: string | null;
  students: LinkedStudent[];
  created_at: string;
}

export interface ParentCreate {
  full_name: string;
  phone?: string | null;
  email?: string | null;
  preferred_channel?: Channel;
  preferred_language?: Language;
  whatsapp_phone?: string | null;
  student_ids?: string[];
  relation?: string | null;
}

export interface LinkStudentRequest {
  student_id: string;
  relation?: string | null;
}

export interface MagicLinkOut {
  url: string;
}

/** Short-lived token for `?access_token=` on this lesson's media URLs (GET /api/lessons/{id}/media-token). */
export interface MediaToken {
  access_token: string;
  token_type?: string;
  lesson_id: string;
  expires_at: string;
}

export interface Notification {
  id: string;
  parent_id: string | null;
  lesson_id: string | null;
  channel: Channel;
  status: NotificationStatus;
  error: string | null;
  payload: Record<string, unknown>;
  sent_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------- lessons

export interface Lesson {
  id: string;
  student_id: string;
  student_name: string;
  teacher_id: string;
  teacher_name: string;
  subject: string;
  scheduled_at: string;
  duration_sec: number | null;
  media_kind?: MediaKind;
  source_filename?: string | null;
  status: LessonStatus;
  status_message: string | null;
  error: string | null;
  stage_index: number | null;
  stage_total: number;
  has_transcript: boolean;
  has_analysis: boolean;
  report_kinds: ReportKind[];
  language_hints?: string[];
  consent_confirmed?: boolean;
  created_at: string;
  processed_at: string | null;
}

export interface Frame {
  id: string;
  index: number;
  timestamp_sec: number;
  url: string;
  reason: FrameReason;
  width: number | null;
  height: number | null;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  speaker: string;
  text: string;
  confidence?: number | null;
}

export interface Transcript {
  provider: string;
  language: string | null;
  segments: TranscriptSegment[];
  full_text: string;
  speaker_map?: Record<string, unknown>;
  word_count?: number;
  duration_sec?: number | null;
}

export interface Analysis {
  provider: string;
  model: string | null;
  prompt_version?: string;
  data: LessonAnalysis;
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms?: number | null;
  created_at?: string;
}

/** Report data shapes are produced by another module — rendered defensively. */
export interface Report {
  kind: ReportKind;
  language: Language;
  data: Record<string, unknown>;
  markdown: string;
  telegram_html: string | null;
  version: number;
  created_at: string;
}

export interface Dashboard {
  lessons_total: number;
  lessons_last_7_days: number;
  lessons_processing: number;
  lessons_failed?: number;
  share_objectives_checked: number | null;
  /** null for the teacher role — the methodist-review indicator is manager-only. */
  needs_review_count: number | null;
  recent_lessons: Lesson[];
  needs_review_lessons: Lesson[];
}

// ---------------------------------------------------------------- progress (staff + parent)

export interface SkillEvidence {
  lesson_id: string;
  /** ISO date of the lesson the entry comes from (lets the UI group evidence by lesson). */
  lesson_at?: string | null;
  timestamp_sec: number | null;
  note: string;
  status?: SkillStatus | null;
  /** summary (one per lesson) | evidence | error */
  kind?: string | null;
}

export interface SkillProgress {
  skill_key: string;
  skill_name: string;
  status: SkillStatus;
  times_seen: number;
  times_struggled: number;
  last_seen_lesson_id: string | null;
  first_seen_lesson_id: string | null;
  evidence: SkillEvidence[];
}

export interface LessonProgressPoint {
  lesson_id: string;
  scheduled_at: string;
  topic: string | null;
  errors_count: number;
  independent_successes_count: number;
  tasks_total: number;
  tasks_solved_independently: number;
}

export interface SkillsCounts {
  struggling: number;
  developing: number;
  mastered: number;
}

export interface StudentProgress {
  skills: SkillProgress[];
  lessons: LessonProgressPoint[];
  counts: SkillsCounts;
}

// ---------------------------------------------------------------- parent portal

export interface StudentBrief {
  id: string;
  full_name: string;
  grade: number | null;
}

export interface ParentMe {
  id: string;
  full_name: string;
  preferred_channel: Channel;
  preferred_language: Language;
  telegram_linked?: boolean;
  center_name?: string;
}

export interface ParentExchangeResponse {
  access_token: string;
  token_type?: string;
  parent: ParentMe;
  students: StudentBrief[];
}

export interface ParentStudentCard {
  student: StudentBrief;
  last_lesson_at: string | null;
  lessons_count: number;
  skills_summary: SkillsCounts;
}

export interface ParentLessonCard {
  lesson_id: string;
  scheduled_at: string;
  topic_plain: string | null;
  encouragement: string | null;
  what_to_repeat_count: number;
}

export interface ParentFrame {
  index: number;
  timestamp_sec: number;
  url: string;
  /** Plain-language caption (what the analysis saw on this frame); null when unknown. */
  caption?: string | null;
}

/** Parent report data as produced by pipeline/reports.py — rendered defensively. */
export interface ParentReportData {
  topic_plain?: string;
  what_child_did_well?: string[];
  what_was_hard?: string[];
  what_to_repeat?: string[];
  home_task_suggestion?: string | null;
  encouragement?: string;
  progress_note?: string | null;
  topic?: string;
  date?: string;
  child_first_name?: string;
  skills_snapshot?: {
    struggling?: string[];
    developing?: string[];
    mastered?: string[];
  };
  [key: string]: unknown;
}

export interface ParentLessonReport {
  lesson_id: string;
  scheduled_at: string;
  teacher_name: string;
  data: ParentReportData;
  /** Only the frames the report points at (errors, successes, visual issues), in time order. */
  frames: ParentFrame[];
  /** Short-lived `?access_token=` for the frame URLs; null when there are no frames. */
  media_token?: string | null;
}

export interface ParentMeUpdate {
  preferred_channel?: Channel;
  preferred_language?: Language;
}
