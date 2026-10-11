/** /parent/students/:id — tabs: lessons (cards) and progress (skills by status, legend, lessons). */
import { Link, NavLink, useParams, useSearchParams } from "react-router-dom";
import { useParentLessons, useParentProgress } from "@/api/queries";
import { useParentAuth } from "@/auth/ParentAuth";
import { LessonDynamics, LessonDynamicsTable, SkillCounts, SkillGroups, SkillLegend } from "@/components/SkillsProgress";
import { BackLink, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { formatDateLong, pluralize } from "@/lib/format";

export function ParentStudentPage() {
  const { id = "" } = useParams();
  const [sp] = useSearchParams();
  const view = sp.get("view") === "progress" ? "progress" : "lessons";
  const { students } = useParentAuth();
  const student = students.find((s) => s.id === id);
  const lessons = useParentLessons(id);
  const progress = useParentProgress(id);
  const lessonLink = (lessonId: string) => `/parent/students/${id}/lessons/${lessonId}`;

  return (
    <>
      <BackLink to="/parent">Все дети</BackLink>
      <h1 style={{ fontSize: "var(--text-xl)", marginTop: 8 }}>{student?.full_name ?? "Ребёнок"}</h1>
      <nav className="parent-nav" aria-label="Разделы">
        <NavLink to={`/parent/students/${id}`} end className={view === "lessons" ? "active" : ""}>
          Уроки
        </NavLink>
        <NavLink to={`/parent/students/${id}?view=progress`} className={view === "progress" ? "active" : ""}>
          Прогресс
        </NavLink>
      </nav>

      {view === "lessons" && (
        <>
          {lessons.isLoading && <Skeleton lines={5} />}
          {lessons.isError && <ErrorState error={lessons.error} onRetry={() => void lessons.refetch()} />}
          {lessons.data && lessons.data.length === 0 && (
            <EmptyState title="Отчётов пока нет" text="Как только урок будет обработан, отчёт появится здесь и придёт вам сообщением." />
          )}
          <div className="stack">
            {lessons.data?.map((l) => (
              <Link key={l.lesson_id} to={lessonLink(l.lesson_id)} className="lesson-card">
                <span className="xs faint">{formatDateLong(l.scheduled_at)}</span>
                <div className="topic">{l.topic_plain ?? "Урок русского языка"}</div>
                {l.encouragement && <div className="encouragement">{l.encouragement}</div>}
                {l.what_to_repeat_count > 0 && (
                  <div className="xs mt-2" style={{ color: "var(--color-primary)" }}>
                    Что повторить дома: {pluralize(l.what_to_repeat_count, ["пункт", "пункта", "пунктов"])}
                  </div>
                )}
              </Link>
            ))}
          </div>
        </>
      )}

      {view === "progress" && (
        <>
          {progress.isLoading && <Skeleton lines={6} />}
          {progress.isError && <ErrorState error={progress.error} onRetry={() => void progress.refetch()} />}
          {progress.data && (
            <div className="stack">
              <div className="section">
                <h3>Навыки сейчас</h3>
                <SkillCounts counts={progress.data.counts} />
                <div className="mt-4">
                  <SkillLegend />
                </div>
              </div>
              <div className="section">
                <h3>По темам</h3>
                <SkillGroups skills={progress.data.skills} lessonLink={lessonLink} />
              </div>
              <div className="section">
                <h3>От урока к уроку</h3>
                <p className="small muted">Как менялось число ошибок и самостоятельных успехов.</p>
                <LessonDynamics lessons={progress.data.lessons} lessonLink={lessonLink} />
                <div className="mt-4">
                  <LessonDynamicsTable lessons={progress.data.lessons} lessonLink={lessonLink} />
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
