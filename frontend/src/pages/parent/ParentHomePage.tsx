/** /parent — children cards: last lesson, lessons count, skills summary. */
import { Link } from "react-router-dom";
import { useParentStudents } from "@/api/queries";
import { useParentAuth } from "@/auth/ParentAuth";
import { SkillCounts } from "@/components/SkillsProgress";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { formatDateLong, pluralize } from "@/lib/format";

export function ParentHomePage() {
  const { parent } = useParentAuth();
  const q = useParentStudents();
  const firstName = parent?.full_name?.split(" ")[0];

  return (
    <>
      <h1 style={{ fontSize: "var(--text-xl)" }}>{firstName ? `Здравствуйте, ${firstName}!` : "Здравствуйте!"}</h1>
      <p className="muted">Здесь собраны отчёты о занятиях и прогресс вашего ребёнка.</p>
      {q.isLoading && <Skeleton lines={5} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && q.data.length === 0 && (
        <EmptyState title="Пока нет привязанных детей" text="Попросите учебный центр привязать вашего ребёнка к вашему аккаунту." />
      )}
      <div className="stack">
        {q.data?.map((card) => (
          <Link key={card.student.id} to={`/parent/students/${card.student.id}`} className="child-card">
            <h3>
              {card.student.full_name}
              {card.student.grade ? <span className="muted" style={{ fontWeight: 400 }}> · {card.student.grade} класс</span> : null}
            </h3>
            <div className="small muted">
              {card.lessons_count > 0
                ? `${pluralize(card.lessons_count, ["урок", "урока", "уроков"])} · последний ${formatDateLong(card.last_lesson_at)}`
                : "Обработанных уроков пока нет"}
            </div>
            <SkillCounts counts={card.skills_summary} />
            <div className="small mt-2" style={{ color: "var(--color-primary)" }}>
              Открыть уроки и прогресс →
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
