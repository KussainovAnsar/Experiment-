import { Link } from "react-router-dom";
import { useDashboard } from "@/api/queries";
import { useStaffAuth } from "@/auth/StaffAuth";
import { LessonTable } from "@/components/LessonTable";
import { EmptyState, ErrorState, PageHeader, Skeleton, Stat } from "@/components/ui";
import { formatPercent } from "@/lib/format";

export function DashboardPage() {
  const { user } = useStaffAuth();
  const q = useDashboard();

  return (
    <>
      <PageHeader
        title="Обзор центра"
        subtitle={user?.center_name ? `${user.center_name} — сводка по обработанным урокам` : "Сводка по обработанным урокам"}
        actions={
          <Link to="/lessons/upload" className="btn btn-primary">
            Загрузить запись
          </Link>
        }
      />
      {q.isLoading && (
        <div className="card">
          <Skeleton lines={5} />
        </div>
      )}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <>
          <div className="grid grid-4">
            <Stat label="Уроков всего" value={q.data.lessons_total} />
            <Stat label="За последние 7 дней" value={q.data.lessons_last_7_days} />
            <Stat
              label="Цели проверены"
              value={formatPercent(q.data.share_objectives_checked)}
              hint="Доля уроков, где преподаватель проверил достижение целей"
            />
            <Stat
              label="Требуют разбора методиста"
              value={q.data.needs_review_count}
              tone={q.data.needs_review_count > 0 ? "warn" : undefined}
              hint={q.data.lessons_processing > 0 ? `В обработке сейчас: ${q.data.lessons_processing}` : undefined}
            />
          </div>

          <div className="card mt-4">
            <div className="card-title">
              <h2>Требуют внимания</h2>
              <span className="small muted">Уроки с пометкой «нужен разбор методиста»</span>
            </div>
            {q.data.needs_review_lessons.length === 0 ? (
              <EmptyState title="Пока ничего не требует разбора" text="Когда анализ отметит урок как требующий внимания, он появится здесь." />
            ) : (
              <LessonTable lessons={q.data.needs_review_lessons} />
            )}
          </div>

          <div className="card">
            <div className="card-title">
              <h2>Последние уроки</h2>
              <Link to="/lessons" className="small">
                Все уроки →
              </Link>
            </div>
            {q.data.recent_lessons.length === 0 ? (
              <EmptyState
                title="Уроков ещё нет"
                text="Загрузите первую запись урока — через несколько минут здесь появятся отчёты."
                action={
                  <Link to="/lessons/upload" className="btn btn-primary mt-2">
                    Загрузить запись
                  </Link>
                }
              />
            ) : (
              <LessonTable lessons={q.data.recent_lessons} />
            )}
          </div>
        </>
      )}
    </>
  );
}
