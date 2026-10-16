/** React Query hooks and query keys. Lessons in progress auto-refetch every 3 s. */
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";
import { isLessonActive } from "@/lib/status";
import type { Lesson, Page, ReportKind, Subject } from "@/types/api";
import { parentApi } from "./parent";
import { centerApi, lessonsApi, parentsApi, referenceApi, studentsApi, teachersApi, type LessonListParams } from "./staff";

export const LESSON_POLL_MS = 3000;
/** Media tokens live ~10 minutes (MEDIA_TOKEN_TTL_MINUTES); refresh them a bit earlier while the page is open. */
export const MEDIA_TOKEN_REFRESH_MS = 8 * 60 * 1000;

export const keys = {
  me: ["auth", "me"] as const,
  dashboard: ["dashboard"] as const,
  lessons: (params: LessonListParams) => ["lessons", params] as const,
  lesson: (id: string) => ["lesson", id] as const,
  transcript: (id: string) => ["lesson", id, "transcript"] as const,
  frames: (id: string) => ["lesson", id, "frames"] as const,
  analysis: (id: string) => ["lesson", id, "analysis"] as const,
  reports: (id: string) => ["lesson", id, "reports"] as const,
  mediaToken: (id: string) => ["lesson", id, "media-token"] as const,
  report: (id: string, kind: ReportKind) => ["lesson", id, "report", kind] as const,
  subjects: ["reference", "subjects"] as const,
  teachers: ["teachers"] as const,
  students: ["students"] as const,
  student: (id: string) => ["student", id] as const,
  studentProgress: (id: string) => ["student", id, "progress"] as const,
  parents: ["parents"] as const,
  parentMe: ["parent", "me"] as const,
  parentStudents: ["parent", "students"] as const,
  parentLessons: (sid: string) => ["parent", "students", sid, "lessons"] as const,
  parentLesson: (sid: string, lid: string) => ["parent", "students", sid, "lessons", lid] as const,
  parentProgress: (sid: string) => ["parent", "students", sid, "progress"] as const,
};

function pageHasActive(page: Page<Lesson> | undefined): boolean {
  return Boolean(page?.items.some((l) => isLessonActive(l.status)));
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: centerApi.dashboard,
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && d.lessons_processing > 0 ? LESSON_POLL_MS : false;
    },
  });
}

export function useLessons(params: LessonListParams) {
  return useQuery({
    queryKey: keys.lessons(params),
    queryFn: () => lessonsApi.list(params),
    placeholderData: (prev) => prev,
    refetchInterval: (q) => (pageHasActive(q.state.data) ? LESSON_POLL_MS : false),
  });
}

export function useLesson(id: string | undefined, options?: Partial<UseQueryOptions<Lesson>>) {
  return useQuery<Lesson>({
    queryKey: keys.lesson(id ?? ""),
    queryFn: () => lessonsApi.get(id!),
    enabled: Boolean(id),
    refetchInterval: (q) => {
      const l = q.state.data;
      return l && isLessonActive(l.status) ? LESSON_POLL_MS : false;
    },
    ...options,
  });
}

export function useTranscript(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.transcript(id), queryFn: () => lessonsApi.transcript(id), enabled });
}

export function useFrames(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.frames(id), queryFn: () => lessonsApi.frames(id), enabled });
}

export function useAnalysis(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.analysis(id), queryFn: () => lessonsApi.analysis(id), enabled });
}

export function useReports(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.reports(id), queryFn: () => lessonsApi.reports(id), enabled });
}

export function useLessonMediaToken(id: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.mediaToken(id),
    queryFn: () => lessonsApi.mediaToken(id),
    enabled,
    staleTime: MEDIA_TOKEN_REFRESH_MS,
    refetchInterval: MEDIA_TOKEN_REFRESH_MS,
    refetchIntervalInBackground: true,
  });
}

/** Subject codes and titles; static reference data, fetched once per session. */
export function useSubjects() {
  return useQuery({ queryKey: keys.subjects, queryFn: referenceApi.subjects, staleTime: Infinity, gcTime: Infinity });
}

/** Title for a subject code; unknown codes (or the list not loaded yet) fall back to the code itself. */
export function subjectTitle(subjects: readonly Subject[] | undefined, code: string): string {
  return subjects?.find((s) => s.code === code)?.title ?? code;
}

export function useTeachers() {
  return useQuery({ queryKey: keys.teachers, queryFn: () => teachersApi.list() });
}

export function useStudents() {
  return useQuery({ queryKey: keys.students, queryFn: () => studentsApi.list() });
}

export function useStudent(id: string) {
  return useQuery({ queryKey: keys.student(id), queryFn: () => studentsApi.get(id) });
}

export function useStudentProgress(id: string) {
  return useQuery({ queryKey: keys.studentProgress(id), queryFn: () => studentsApi.progress(id) });
}

export function useParents() {
  return useQuery({ queryKey: keys.parents, queryFn: () => parentsApi.list() });
}

export function useInvalidate() {
  const qc = useQueryClient();
  return (key: readonly unknown[]) => qc.invalidateQueries({ queryKey: key });
}

export function useSendParentReport(lessonId: string) {
  return useMutation({ mutationFn: () => lessonsApi.sendParentReport(lessonId) });
}

export function useReprocessLesson(lessonId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => lessonsApi.reprocess(lessonId),
    onSuccess: (lesson) => {
      qc.setQueryData(keys.lesson(lessonId), lesson);
      void qc.invalidateQueries({ queryKey: ["lesson", lessonId] });
    },
  });
}

// ---------------------------------------------------------------- parent portal

export function useParentMe() {
  return useQuery({ queryKey: keys.parentMe, queryFn: parentApi.me });
}

export function useParentStudents() {
  return useQuery({ queryKey: keys.parentStudents, queryFn: parentApi.students });
}

export function useParentLessons(studentId: string) {
  return useQuery({ queryKey: keys.parentLessons(studentId), queryFn: () => parentApi.lessons(studentId) });
}

export function useParentLesson(studentId: string, lessonId: string) {
  return useQuery({
    queryKey: keys.parentLesson(studentId, lessonId),
    queryFn: () => parentApi.lesson(studentId, lessonId),
    // the response carries the media token for the frames: keep it fresh while the page stays open
    refetchInterval: MEDIA_TOKEN_REFRESH_MS,
  });
}

export function useParentProgress(studentId: string) {
  return useQuery({ queryKey: keys.parentProgress(studentId), queryFn: () => parentApi.progress(studentId) });
}
