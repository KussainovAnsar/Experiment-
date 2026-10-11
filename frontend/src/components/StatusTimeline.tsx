/** Pipeline status timeline for the lesson page (CONTRACT §4.1). */
import { PIPELINE_STAGES, LESSON_STATUS_META, lessonProgress } from "@/lib/status";
import type { Lesson } from "@/types/api";
import { ProgressBar } from "./ui";

export function StatusTimeline({ lesson }: { lesson: Lesson }) {
  const { status, stage_index, stage_total, status_message, error } = lesson;
  const failed = status === "failed";
  const currentIdx = stage_index ?? PIPELINE_STAGES.findIndex((s) => s.status === status);
  const meta = LESSON_STATUS_META[status];
  const value = lessonProgress(stage_index, stage_total, status);

  return (
    <div className="card">
      <div className="card-title">
        <h3>Обработка записи</h3>
        <span className={`small ${failed ? "priority-high" : "muted"}`}>
          {failed ? "Остановлено с ошибкой" : meta?.active ? `${Math.round(value * 100)} %` : "Завершено"}
        </span>
      </div>
      <div className="timeline" aria-label="Этапы обработки">
        {PIPELINE_STAGES.map((stage, i) => {
          let cls = "timeline-step";
          if (failed) {
            if (i < currentIdx || currentIdx < 0) cls += " done";
            else if (i === currentIdx) cls += " failed";
          } else if (status === "done") {
            cls += " done";
          } else if (i < currentIdx) {
            cls += " done";
          } else if (i === currentIdx) {
            cls += " current";
          }
          return (
            <div key={stage.status} className={cls}>
              <div className="bar" />
              <span>{stage.label}</span>
            </div>
          );
        })}
      </div>
      {meta?.active && (
        <div className="mt-4">
          <ProgressBar value={value} indeterminate={value === 0} />
          <p className="small muted mt-2 mb-0">
            Сейчас: {meta.label}
            {status_message ? ` — ${status_message}` : ""}. Страница обновляется автоматически.
          </p>
        </div>
      )}
      {failed && (
        <div className="error-box mt-4" role="alert">
          <div style={{ minWidth: 0 }}>
            <strong>Ошибка обработки.</strong> {status_message || "Причина не указана."}
            {error && (
              <details className="mt-2">
                <summary className="small">Технические подробности</summary>
                <pre className="xs mt-2 mb-0" style={{ whiteSpace: "pre-wrap" }}>{error}</pre>
              </details>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
