import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useLessons, useStudents, useTeachers } from "@/api/queries";
import { useStaffAuth } from "@/auth/StaffAuth";
import { LessonTable } from "@/components/LessonTable";
import { EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { LESSON_STATUS_META } from "@/lib/status";
import type { LessonStatus } from "@/types/api";

const STATUSES = Object.keys(LESSON_STATUS_META) as LessonStatus[];
const PAGE_SIZE = 20;

export function LessonsPage() {
  const { isTeacher } = useStaffAuth();
  const [sp, setSp] = useSearchParams();
  const params = useMemo(
    () => ({
      student_id: sp.get("student_id") ?? undefined,
      teacher_id: sp.get("teacher_id") ?? undefined,
      status: ((sp.get("status") ?? "") as LessonStatus | ""),
      page: Number(sp.get("page") ?? "1") || 1,
      page_size: PAGE_SIZE,
    }),
    [sp],
  );
  const q = useLessons(params);
  const students = useStudents();
  const teachers = useTeachers();

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(sp);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key !== "page") next.delete("page");
    setSp(next, { replace: true });
  };

  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasFilters = Boolean(params.student_id || params.teacher_id || params.status);

  return (
    <>
      <PageHeader
        title="Уроки"
        subtitle="Записи уроков и статус их обработки. Список обновляется автоматически, пока идёт обработка."
        actions={
          <Link to="/lessons/upload" className="btn btn-primary">
            Загрузить запись
          </Link>
        }
      />
      <div className="card">
        <div className="form-row" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
          <div className="field">
            <label htmlFor="f-student">Ученик</label>
            <select id="f-student" className="select" value={params.student_id ?? ""} onChange={(e) => setParam("student_id", e.target.value)}>
              <option value="">Все ученики</option>
              {students.data?.items.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.full_name}
                </option>
              ))}
            </select>
          </div>
          {!isTeacher && (
            <div className="field">
              <label htmlFor="f-teacher">Преподаватель</label>
              <select id="f-teacher" className="select" value={params.teacher_id ?? ""} onChange={(e) => setParam("teacher_id", e.target.value)}>
                <option value="">Все преподаватели</option>
                {teachers.data?.items.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.full_name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="field">
            <label htmlFor="f-status">Статус</label>
            <select id="f-status" className="select" value={params.status} onChange={(e) => setParam("status", e.target.value)}>
              <option value="">Любой статус</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {LESSON_STATUS_META[s].label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-4">
          {q.isLoading && <Skeleton lines={6} />}
          {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
          {q.data && q.data.items.length === 0 && (
            <EmptyState
              title={hasFilters ? "По выбранным фильтрам уроков нет" : "Уроков ещё нет"}
              text={hasFilters ? "Попробуйте изменить фильтры." : "Загрузите первую запись урока."}
              action={
                hasFilters ? (
                  <button type="button" className="btn mt-2" onClick={() => setSp(new URLSearchParams(), { replace: true })}>
                    Сбросить фильтры
                  </button>
                ) : (
                  <Link to="/lessons/upload" className="btn btn-primary mt-2">
                    Загрузить запись
                  </Link>
                )
              }
            />
          )}
          {q.data && q.data.items.length > 0 && <LessonTable lessons={q.data.items} hideTeacher={isTeacher} />}
        </div>

        {pages > 1 && (
          <div className="row row-between mt-4">
            <span className="small muted">
              Всего: {total}. Страница {params.page} из {pages}
            </span>
            <div className="row">
              <button type="button" className="btn btn-sm" disabled={params.page <= 1} onClick={() => setParam("page", String(params.page - 1))}>
                ← Назад
              </button>
              <button type="button" className="btn btn-sm" disabled={params.page >= pages} onClick={() => setParam("page", String(params.page + 1))}>
                Вперёд →
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
