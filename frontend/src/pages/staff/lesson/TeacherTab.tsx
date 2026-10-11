/** "Для учителя": episodes (error → re-explanation → repeated), recommendations, visual issues. */
import { FrameRefs, FrameInline } from "@/components/FrameThumb";
import { Timecode } from "@/components/Timecode";
import { Badge, EmptyState, Skeleton, SkillChip } from "@/components/ui";
import { formatTimecode } from "@/lib/format";
import type { Frame, Report } from "@/types/api";
import { PRIORITY_LABELS, SIGNAL_STRENGTH_LABELS, type LessonAnalysis, type StudentError, type TeachingEvent } from "@/types/analysis";
import { ReportDataView, ReportMarkdown } from "./ReportData";

interface Episode {
  error: StudentError;
  reExplanation: TeachingEvent | null;
}

/** Pair each error with the first re-explanation that follows it (within the lesson). */
function buildEpisodes(a: LessonAnalysis): Episode[] {
  const reExplanations = a.timeline.filter((e) => e.kind === "re_explanation").sort((x, y) => x.start - y.start);
  return [...a.errors]
    .sort((x, y) => x.timestamp - y.timestamp)
    .map((error) => ({
      error,
      reExplanation: reExplanations.find((e) => e.start >= error.timestamp) ?? null,
    }));
}

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 } as const;

export function TeacherTab({
  analysis,
  report,
  frames,
  loading,
  ready,
}: {
  analysis: LessonAnalysis | undefined;
  report: Report | undefined;
  frames: Frame[];
  loading: boolean;
  ready: boolean;
}) {
  if (!ready) {
    return <EmptyState title="Отчёт для учителя ещё не готов" text="Он появится автоматически после завершения анализа." />;
  }
  if (loading && !analysis) return <Skeleton lines={8} />;
  if (!analysis) {
    return (
      <>
        <EmptyState title="Анализ урока недоступен" text="Попробуйте обновить страницу или запустить обработку заново." />
        {report && <ReportDataView data={report.data} frames={frames} />}
      </>
    );
  }

  const episodes = buildEpisodes(analysis);
  const recommendations = [...analysis.teacher_recommendations].sort(
    (a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority],
  );
  const visualIssues = analysis.visual_observations.filter((v) => v.issue);
  const frameByIndex = new Map(frames.map((f) => [f.index, f]));
  const objectives = analysis.learning_objectives;

  return (
    <div className="stack" style={{ gap: "var(--space-5)" }}>
      <section>
        <h3>Коротко об уроке</h3>
        <p>{analysis.lesson_summary}</p>
        {objectives.length > 0 && (
          <>
            <h4>Цели урока</h4>
            <ul>
              {objectives.map((o, i) => (
                <li key={i}>
                  <Badge tone={o.was_checked ? "success" : "warning"}>{o.was_checked ? "проверена" : "не проверена"}</Badge> {o.text}
                  {o.evidence && <span className="muted small"> — {o.evidence}</span>}
                </li>
              ))}
            </ul>
          </>
        )}
        {analysis.skills.length > 0 && (
          <div className="chips">
            {analysis.skills.map((s) => (
              <SkillChip key={s.skill_key} status={s.status}>
                {s.skill_name}
              </SkillChip>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3>
          Эпизоды с ошибками <span className="tab-count">{episodes.length}</span>
        </h3>
        {episodes.length === 0 ? (
          <p className="muted">Ошибок ученика в этом уроке не зафиксировано.</p>
        ) : (
          episodes.map(({ error, reExplanation }, i) => (
            <article
              key={i}
              className={`episode${error.corrected ? " corrected" : ""}${error.repeated_after_explanation ? " repeated" : ""}`}
            >
              <div className="row">
                <Timecode seconds={error.timestamp} />
                <strong>{error.skill_name}</strong>
              </div>
              <p className="mb-0 mt-2">{error.description}</p>
              {error.quote && <p className="quote">«{error.quote}»</p>}
              <div className="episode-flow">
                <Badge tone="danger">Ошибка</Badge>
                <span className="faint">→</span>
                {reExplanation ? (
                  <Badge tone="info">
                    Переобъяснение в {formatTimecode(reExplanation.start)}
                  </Badge>
                ) : (
                  <Badge tone="neutral">Переобъяснения не было</Badge>
                )}
                <span className="faint">→</span>
                {error.repeated_after_explanation ? (
                  <Badge tone="warning">Ошибка повторилась</Badge>
                ) : error.corrected ? (
                  <Badge tone="success">Исправлена</Badge>
                ) : (
                  <Badge tone="neutral">Не исправлена</Badge>
                )}
              </div>
              {reExplanation && <p className="small muted mt-2 mb-0">{reExplanation.summary}</p>}
              <FrameRefs frameIds={error.frame_ids} frames={frames} />
            </article>
          ))
        )}
      </section>

      <section>
        <h3>
          Рекомендации <span className="tab-count">{recommendations.length}</span>
        </h3>
        {recommendations.length === 0 ? (
          <p className="muted">Рекомендаций нет.</p>
        ) : (
          recommendations.map((r, i) => (
            <div key={i} className="reco">
              <div className="stack" style={{ gap: 4, alignItems: "flex-start" }}>
                <span className={`xs priority-${r.priority}`} style={{ fontWeight: 700, textTransform: "uppercase" }}>
                  {PRIORITY_LABELS[r.priority]}
                </span>
                <Timecode seconds={r.timestamp} />
              </div>
              <div>
                <p className="mb-0">
                  <strong>Наблюдение.</strong> {r.observation}
                </p>
                <p className="mb-0 mt-2">
                  <strong>Что попробовать.</strong> {r.suggestion}
                </p>
              </div>
            </div>
          ))
        )}
      </section>

      {analysis.misconceptions.length > 0 && (
        <section>
          <h3>Возможные пробелы в понимании</h3>
          <div className="stack">
            {analysis.misconceptions.map((m, i) => (
              <div key={i} className="card card-muted" style={{ padding: "10px 14px" }}>
                <div className="row">
                  <strong>{m.skill_key.replace(/_/g, " ")}</strong>
                  <Badge tone={m.signal_strength === "strong" ? "danger" : m.signal_strength === "useful" ? "warning" : "neutral"}>
                    {SIGNAL_STRENGTH_LABELS[m.signal_strength]}
                  </Badge>
                  <span className="xs muted">уверенность {Math.round(m.confidence * 100)} %</span>
                </div>
                <p className="mb-0 mt-2">{m.description}</p>
                {m.evidence.map((e, j) => (
                  <p key={j} className="small muted mb-0 mt-2">
                    <Timecode seconds={e.timestamp} /> {e.description}
                    {e.quote ? ` — «${e.quote}»` : ""}
                  </p>
                ))}
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h3>
          Замечено на экране <span className="tab-count">{visualIssues.length}</span>
        </h3>
        {visualIssues.length === 0 ? (
          <p className="muted">Проблем на доске или в упражнениях не замечено.</p>
        ) : (
          <div className="stack">
            {visualIssues.map((v, i) => {
              const frame = frameByIndex.get(v.frame_id);
              return (
                <div key={i} className="row" style={{ alignItems: "flex-start", gap: 14 }}>
                  {frame ? (
                    <FrameInline frame={frame} />
                  ) : (
                    <Timecode seconds={v.timestamp} label={`кадр #${v.frame_id} · ${formatTimecode(v.timestamp)}`} />
                  )}
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <p className="mb-0">
                      <strong>{v.issue}</strong>
                    </p>
                    <p className="small muted mb-0">{v.description}</p>
                    {v.text_detected && <p className="quote small">Текст на кадре: «{v.text_detected}»</p>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {analysis.independent_successes.length > 0 && (
        <section>
          <h3>Что ученик сделал самостоятельно</h3>
          <ul>
            {analysis.independent_successes.map((e, i) => (
              <li key={i}>
                <Timecode seconds={e.timestamp} /> {e.description}
                {e.quote ? <span className="muted"> — «{e.quote}»</span> : null}
                <FrameRefs frameIds={e.frame_ids} frames={frames} />
              </li>
            ))}
          </ul>
        </section>
      )}

      {(analysis.limitations.length > 0 || analysis.language_notes) && (
        <section>
          <h3>Оговорки анализа</h3>
          <ul>
            {analysis.limitations.map((l, i) => (
              <li key={i} className="muted">
                {l}
              </li>
            ))}
            {analysis.language_notes && <li className="muted">{analysis.language_notes}</li>}
          </ul>
        </section>
      )}

      {report && (
        <section>
          <h3 className="muted">Отчёт для учителя (версия {report.version})</h3>
          <ReportDataView
            data={report.data}
            frames={frames}
            exclude={[
              "episodes",
              "recommendations",
              "visual_issues",
              "misconceptions",
              "summary",
              "lesson_summary",
              "topic",
              "subject",
              "skills",
              "limitations",
              "language_notes",
              "objectives",
              "learning_objectives",
              "independent_successes",
              "timeline",
              "teacher_name",
              "student_first_name",
            ]}
          />
          <ReportMarkdown markdown={report.markdown} />
        </section>
      )}
    </div>
  );
}
