import { Link, useParams } from "react-router-dom";
import { useLessons, useStudent, useStudentProgress } from "@/api/queries";
import { LessonTable } from "@/components/LessonTable";
import { LessonDynamics, LessonDynamicsTable, SkillCounts, SkillGroups, SkillLegend } from "@/components/SkillsProgress";
import { BackLink, EmptyState, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { LANGUAGE_LABELS } from "@/lib/status";

export function StudentDetailPage() {
  const { id = "" } = useParams();
  const student = useStudent(id);
  const progress = useStudentProgress(id);
  const lessons = useLessons({ student_id: id, page: 1, page_size: 50 });
  const lessonLink = (lessonId: string) => `/lessons/${lessonId}`;

  if (student.isLoading) {
    return (
      <div className="card">
        <Skeleton lines={6} />
      </div>
    );
  }
  if (student.isError || !student.data) {
    return (
      <>
        <BackLink to="/students">К списку учеников</BackLink>
        <div className="mt-4">
          <ErrorState error={student.error ?? new Error("Ученик не найден")} onRetry={() => void student.refetch()} />
        </div>
      </>
    );
  }
  const s = student.data;

  return (
    <>
      <BackLink to="/students">К списку учеников</BackLink>
      <PageHeader
        title={s.full_name}
        subtitle={[s.grade ? `${s.grade} класс` : null, s.school, `родной язык: ${LANGUAGE_LABELS[s.native_language] ?? s.native_language}`].filter(Boolean).join(" · ")}
        actions={
          <Link to={`/lessons/upload`} className="btn btn-primary">
            Загрузить урок
          </Link>
        }
      />

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">
            <h2>Навыки</h2>
          </div>
          {progress.isLoading && <Skeleton lines={6} />}
          {progress.isError && <ErrorState error={progress.error} onRetry={() => void progress.refetch()} />}
          {progress.data && (
            <>
              <SkillCounts counts={progress.data.counts} />
              <div className="mt-4">
                <SkillGroups skills={progress.data.skills} lessonLink={lessonLink} />
              </div>
            </>
          )}
        </div>
        <div className="stack">
          <div className="card">
            <h3>Динамика по урокам</h3>
            {progress.data && <LessonDynamics lessons={progress.data.lessons} lessonLink={lessonLink} />}
            {progress.data && (
              <div className="mt-4">
                <LessonDynamicsTable lessons={progress.data.lessons} lessonLink={lessonLink} />
              </div>
            )}
          </div>
          <div className="card">
            <h3>Как читать статусы</h3>
            <SkillLegend />
          </div>
        </div>
      </div>

      <div className="card mt-4">
        <div className="card-title">
          <h2>Уроки</h2>
          <Link to={`/lessons?student_id=${id}`} className="small">
            Все уроки ученика →
          </Link>
        </div>
        {lessons.isLoading && <Skeleton lines={4} />}
        {lessons.isError && <ErrorState error={lessons.error} onRetry={() => void lessons.refetch()} />}
        {lessons.data && lessons.data.items.length === 0 && <EmptyState title="Уроков пока нет" text="Загрузите первую запись урока этого ученика." />}
        {lessons.data && lessons.data.items.length > 0 && <LessonTable lessons={lessons.data.items} hideStudent />}
      </div>
    </>
  );
}
