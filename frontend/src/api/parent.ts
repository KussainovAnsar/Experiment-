/** Typed parent-portal API calls (CONTRACT §6, parent section). All use the parent realm. */
import { http } from "./client";
import type {
  ParentExchangeResponse,
  ParentLessonCard,
  ParentLessonReport,
  ParentMe,
  ParentMeUpdate,
  ParentStudentCard,
  StudentProgress,
} from "@/types/api";

const realm = "parent" as const;

export const parentApi = {
  exchange: (token: string) =>
    http.post<ParentExchangeResponse>("/api/parent/auth/exchange", { token }, { realm, silent401: true }),
  me: () => http.get<ParentMe>("/api/parent/me", { realm }),
  updateMe: (body: ParentMeUpdate) => http.patch<ParentMe>("/api/parent/me", body, { realm }),
  students: () => http.get<ParentStudentCard[]>("/api/parent/students", { realm }),
  lessons: (studentId: string) =>
    http.get<ParentLessonCard[]>(`/api/parent/students/${studentId}/lessons`, { realm }),
  lesson: (studentId: string, lessonId: string) =>
    http.get<ParentLessonReport>(`/api/parent/students/${studentId}/lessons/${lessonId}`, { realm }),
  progress: (studentId: string) =>
    http.get<StudentProgress>(`/api/parent/students/${studentId}/progress`, { realm }),
};
