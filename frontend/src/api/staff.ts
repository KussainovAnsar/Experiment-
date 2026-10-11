/** Typed staff API calls (CONTRACT §6, staff section). */
import { http, uploadWithProgress, type UploadProgress } from "./client";
import type {
  Analysis,
  Center,
  Dashboard,
  Frame,
  Lesson,
  LessonStatus,
  LinkStudentRequest,
  LoginRequest,
  MagicLinkOut,
  Notification,
  Page,
  Parent,
  ParentCreate,
  RegisterCenterRequest,
  Report,
  ReportKind,
  StaffUser,
  Student,
  StudentCreate,
  StudentProgress,
  Teacher,
  TeacherCreate,
  TokenResponse,
  Transcript,
} from "@/types/api";

export const authApi = {
  login: (body: LoginRequest) => http.post<TokenResponse>("/api/auth/login", body, { silent401: true }),
  registerCenter: (body: RegisterCenterRequest) =>
    http.post<TokenResponse>("/api/auth/register-center", body, { silent401: true }),
  me: () => http.get<StaffUser>("/api/auth/me"),
};

export const centerApi = {
  me: () => http.get<Center>("/api/centers/me"),
  dashboard: () => http.get<Dashboard>("/api/dashboard"),
};

export interface LessonListParams {
  student_id?: string;
  teacher_id?: string;
  status?: LessonStatus | "";
  page?: number;
  page_size?: number;
}

export interface LessonUploadFields {
  file: File;
  student_id: string;
  teacher_id?: string;
  scheduled_at?: string;
  subject?: string;
  consent_confirmed: boolean;
  language_hints: string[];
}

export const lessonsApi = {
  list: (params: LessonListParams) =>
    http.get<Page<Lesson>>("/api/lessons", {
      params: {
        student_id: params.student_id,
        teacher_id: params.teacher_id,
        status: params.status || undefined,
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
      },
    }),
  get: (id: string) => http.get<Lesson>(`/api/lessons/${id}`),
  reprocess: (id: string) => http.post<Lesson>(`/api/lessons/${id}/reprocess`),
  transcript: (id: string) => http.get<Transcript>(`/api/lessons/${id}/transcript`),
  frames: (id: string) => http.get<Frame[]>(`/api/lessons/${id}/frames`),
  analysis: (id: string) => http.get<Analysis>(`/api/lessons/${id}/analysis`),
  reports: (id: string) => http.get<Report[]>(`/api/lessons/${id}/reports`),
  report: (id: string, kind: ReportKind) => http.get<Report>(`/api/lessons/${id}/reports/${kind}`),
  sendParentReport: (id: string) => http.post<Notification[]>(`/api/lessons/${id}/reports/parent/send`),
  upload: (fields: LessonUploadFields, onProgress: (p: UploadProgress) => void, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", fields.file, fields.file.name);
    form.append("student_id", fields.student_id);
    if (fields.teacher_id) form.append("teacher_id", fields.teacher_id);
    if (fields.scheduled_at) form.append("scheduled_at", fields.scheduled_at);
    form.append("subject", fields.subject ?? "russian");
    form.append("consent_confirmed", fields.consent_confirmed ? "true" : "false");
    form.append("language_hints", fields.language_hints.join(","));
    return uploadWithProgress<Lesson>("/api/lessons", form, onProgress, { signal });
  },
  /** Media URL of the source recording (append access_token via withAccessToken). */
  sourceUrl: (id: string) => `/api/media/lessons/${id}/source`,
};

export const teachersApi = {
  list: (page = 1, pageSize = 100) =>
    http.get<Page<Teacher>>("/api/teachers", { params: { page, page_size: pageSize } }),
  create: (body: TeacherCreate) => http.post<Teacher>("/api/teachers", body),
  remove: (id: string) => http.delete<void>(`/api/teachers/${id}`),
};

export const studentsApi = {
  list: (page = 1, pageSize = 100) =>
    http.get<Page<Student>>("/api/students", { params: { page, page_size: pageSize } }),
  get: (id: string) => http.get<Student>(`/api/students/${id}`),
  create: (body: StudentCreate) => http.post<Student>("/api/students", body),
  remove: (id: string) => http.delete<void>(`/api/students/${id}`),
  progress: (id: string) => http.get<StudentProgress>(`/api/students/${id}/progress`),
};

export const parentsApi = {
  list: (page = 1, pageSize = 100) =>
    http.get<Page<Parent>>("/api/parents", { params: { page, page_size: pageSize } }),
  create: (body: ParentCreate) => http.post<Parent>("/api/parents", body),
  remove: (id: string) => http.delete<void>(`/api/parents/${id}`),
  linkStudent: (id: string, body: LinkStudentRequest) =>
    http.post<Parent>(`/api/parents/${id}/link-student`, body),
  magicLink: (id: string) => http.post<MagicLinkOut>(`/api/parents/${id}/magic-link`),
  sendInvite: (id: string) => http.post<Notification>(`/api/parents/${id}/send-invite`),
};
