/** "Для методиста": quality indicator cards, talk-share bars, tasks, summary. No teacher rating. */
import { Timecode } from "@/components/Timecode";
import { EmptyState, Skeleton, SkillChip } from "@/components/ui";
import { formatPercent } from "@/lib/format";
import type { Report } from "@/types/api";
import { EVENT_KIND_LABELS, SPEAKER_LABELS, type LessonAnalysis } from "@/types/analysis";
import { ReportDataView, ReportMarkdown } from "./ReportData";

function Indicator({ ok, label, detail, invert }: { ok: boolean; label: string; detail?: string | null; invert?: boolean }) {
  // `invert`: true means "ok" is the undesirable state (e.g. needs review).
  const good = invert ? !ok : ok;
  return (
    <div className={`indicator ${good ? "indicator-ok" : "indicator-bad"}`}>
      <span className="indicator-icon" aria-hidden="true">
        {good ? "✓" : "!"}
      </span>
      <div>
        <div style={{ fontWeight: 600 }}>{label}</div>
        <div className="small muted">{detail ?? (ok ? "Да" : "Нет")}</div>
      </div>
    </div>
  );
}

export function ManagerTab({
  analysis,
  report,
  loading,
  ready,
}: {
  analysis: LessonAnalysis | undefined;
  report: Report | undefined;
  loading: boolean;
  ready: boolean;
}) {
  if (!ready) {
    return <EmptyState title="Отчёт для методиста ещё не готов" text="Он появится автоматически после завершения анализа." />;
  }
  if (loading && !analysis) return <Skeleton lines={8} />;
  if (!analysis) {
    return (
      <>
        <EmptyState title="Анализ урока недоступен" text="Попробуйте обновить страницу или запустить обработку заново." />
        {report && <ReportDataView data={report.data} />}
      </>
    );
  }

  const m = analysis.manager_indicators;
  const teacherShare = m.teacher_talk_share;
  const studentShare = m.student_talk_share;
  const hasShares = teacherShare !== null || studentShare !== null;
  const t = teacherShare ?? (studentShare !== null ? 1 - studentShare : 0);
  const s = studentShare ?? (teacherShare !== null ? 1 - teacherShare : 0);
  const solvedShare = m.tasks_total > 0 ? m.tasks_solved_independently / m.tasks_total : null;

  return (
    <div className="stack" style={{ gap: "var(--space-5)" }}>
      <section>
        <div className="grid grid-3">
          <Indicator ok={m.objectives_checked} label="Цели проверены" detail={m.objectives_checked ? "Преподаватель проверил достижение целей" : "Проверка целей не наблюдалась"} />
          <Indicator
            ok={!m.difficulties_detected}
            label="Трудности выявлены"
            detail={m.difficulties_detected ? "Зафиксированы ошибки или затруднения ученика" : "Явных затруднений не зафиксировано"}
          />
          <Indicator
            ok={m.needs_methodist_review}
            invert
            label="Требуется разбор методиста"
            detail={m.needs_methodist_review ? m.review_reason ?? "Рекомендуется посмотреть запись" : "Разбор не требуется"}
          />
        </div>
      </section>

      <section className="grid grid-2">
        <div className="card card-muted">
          <h4>Доли речи</h4>
          {hasShares ? (
            <>
              <div className="share-bar" role="img" aria-label={`Учитель ${formatPercent(t)}, ученик ${formatPercent(s)}`}>
                <span className="share-teacher" style={{ width: `${Math.round(t * 100)}%` }} />
                <span className="share-student" style={{ width: `${Math.round(s * 100)}%` }} />
              </div>
              <div className="legend mt-2">
                <span>
                  <i className="legend-dot share-teacher" />
                  Учитель {formatPercent(t)}
                </span>
                <span>
                  <i className="legend-dot share-student" />
                  Ученик {formatPercent(s)}
                </span>
              </div>
            </>
          ) : (
            <p className="muted small mb-0">Доли речи не рассчитаны (нет разделения говорящих).</p>
          )}
        </div>
        <div className="card card-muted">
          <h4>Задания</h4>
          <div className="stat-value">
            {m.tasks_solved_independently} <span className="muted" style={{ fontSize: "var(--text-md)", fontWeight: 500 }}>из {m.tasks_total} решено самостоятельно</span>
          </div>
          {solvedShare !== null && (
            <div className="progress mt-2">
              <div className="progress-bar" style={{ width: `${Math.round(solvedShare * 100)}%` }} />
            </div>
          )}
          <p className="small muted mt-2 mb-0">
            Переобъяснение: {m.re_explanation_happened ? "было" : "не было"}
          </p>
        </div>
      </section>

      <section>
        <h3>Краткое резюме</h3>
        <p>{analysis.lesson_summary}</p>
        {m.lesson_structure_notes && (
          <p className="muted">
            <strong>Структура урока.</strong> {m.lesson_structure_notes}
          </p>
        )}
        {analysis.skills.length > 0 && (
          <div className="chips">
            {analysis.skills.map((sk) => (
              <SkillChip key={sk.skill_key} status={sk.status}>
                {sk.skill_name}
              </SkillChip>
            ))}
          </div>
        )}
      </section>

      {analysis.timeline.length > 0 && (
        <section>
          <h3>Хронология урока</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Событие</th>
                  <th>Кто</th>
                  <th>Что произошло</th>
                </tr>
              </thead>
              <tbody>
                {analysis.timeline.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap">
                      <Timecode seconds={e.start} />
                    </td>
                    <td className="nowrap">{EVENT_KIND_LABELS[e.kind] ?? e.kind}</td>
                    <td className="nowrap muted">{SPEAKER_LABELS[e.speaker] ?? e.speaker}</td>
                    <td>
                      {e.summary}
                      {e.quote && <div className="quote small">«{e.quote}»</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {analysis.limitations.length > 0 && (
        <section>
          <h3>Оговорки анализа</h3>
          <ul>
            {analysis.limitations.map((l, i) => (
              <li key={i} className="muted">
                {l}
              </li>
            ))}
          </ul>
        </section>
      )}

      {report && (
        <section>
          <h3 className="muted">Отчёт для методиста (версия {report.version})</h3>
          <ReportDataView data={report.data} exclude={["indicators", "summary", "lesson_summary", "topic", "subject", "skills", "limitations", "timeline", "talk_shares", "needs_review", "review_reason", "structure_notes", "teacher_name", "student_first_name", "language_notes", "objectives"]} />
          <ReportMarkdown markdown={report.markdown} />
        </section>
      )}
    </div>
  );
}
