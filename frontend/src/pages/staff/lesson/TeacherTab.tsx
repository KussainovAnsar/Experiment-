/** "Для учителя": episodes (error → re-explanation → repeated after it → corrected), recommendations, visual issues. */
import { FrameRefs, FrameInline } from "@/components/FrameThumb";
import { Timecode } from "@/components/Timecode";
import { Badge, EmptyState, Skeleton, SkillChip } from "@/components/ui";
import { formatTimecode } from "@/lib/format";
import type { Frame, Report } from "@/types/api";
import { PRIORITY_LABELS, SIGNAL_STRENGTH_LABELS, type LessonAnalysis, type StudentError } from "@/types/analysis";
import { ReportDataView, ReportMarkdown } from "./ReportData";

/** One error episode as the teacher report tells it. Mirrors `build_episodes` in backend/app/pipeline/reports.py. */
export interface EpisodeView {
  timestamp: number;
  skillName: string;
  description: string;
  quote: string | null;
  frameIds: number[];
  /** The re-explanation attributed to this error (same skill, within the window), if any. */
  reExplanation: { timestamp: number; summary: string } | null;
  /** The LLM's own flag: this error itself happened after the rule had already been explained. */
  repeatedAfterExplanation: boolean;
  /** Timecode of the next same-skill error AFTER the attributed re-explanation (never claimed without one). */
  repeatedAt: string | null;
  corrected: boolean;
}

/** A re-explanation this far after an error is still attributed to it (same value as the backend). */
export const RE_EXPLANATION_WINDOW_SEC = 900;

/**
 * Fallback when the stored teacher report is not available yet: the same attribution rule as the backend —
 * the first re-explanation within the window after the error, unless an error of a *different* skill happened
 * in between (then the teacher was re-explaining that one), and a repeat only after the re-explanation.
 */
export function episodesFromAnalysis(a: LessonAnalysis): EpisodeView[] {
  const errors = [...a.errors].sort((x, y) => x.timestamp - y.timestamp);
  const reExplanations = a.timeline.filter((e) => e.kind === "re_explanation").sort((x, y) => x.start - y.start);
  const findReExplanation = (error: StudentError) =>
    reExplanations.find(
      (ev) =>
        ev.start >= error.timestamp &&
        ev.start <= error.timestamp + RE_EXPLANATION_WINDOW_SEC &&
        !errors.some((o) => o.timestamp > error.timestamp && o.timestamp <= ev.start && o.skill_key !== error.skill_key),
    ) ?? null;
  return errors.map((error, i) => {
    const re = findReExplanation(error);
    const laterSame = errors.slice(i + 1).filter((e) => e.skill_key === error.skill_key);
    const repeated = re ? laterSame.find((e) => e.timestamp > re.start) : undefined;
    return {
      timestamp: error.timestamp,
      skillName: error.skill_name,
      description: error.description,
      quote: error.quote,
      frameIds: error.frame_ids,
      reExplanation: re ? { timestamp: re.start, summary: re.summary } : null,
      repeatedAfterExplanation: error.repeated_after_explanation,
      repeatedAt: repeated ? formatTimecode(repeated.timestamp) : null,
      corrected: error.corrected,
    };
  });
}

/** Episodes from the stored teacher report (`report.data.episodes`), rendered defensively. */
export function episodesFromReport(data: Record<string, unknown> | undefined): EpisodeView[] | null {
  const raw = data?.episodes;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  const out: EpisodeView[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const ep = item as Record<string, unknown>;
    if (typeof ep.timestamp !== "number") continue;
    const re = ep.re_explanation;
    const reObj = typeof re === "object" && re !== null ? (re as Record<string, unknown>) : null;
    const frames = Array.isArray(ep.frames) ? ep.frames : [];
    out.push({
      timestamp: ep.timestamp,
      skillName: typeof ep.skill_name === "string" ? ep.skill_name : String(ep.skill_key ?? ""),
      description: typeof ep.description === "string" ? ep.description : "",
      quote: typeof ep.quote === "string" ? ep.quote : null,
      frameIds: frames
        .map((f) => (typeof f === "object" && f !== null ? (f as Record<string, unknown>).index : undefined))
        .filter((i): i is number => typeof i === "number"),
      reExplanation:
        reObj && typeof reObj.timestamp === "number"
          ? { timestamp: reObj.timestamp, summary: typeof reObj.summary === "string" ? reObj.summary : "" }
          : null,
      repeatedAfterExplanation: ep.repeated_after_explanation === true,
      repeatedAt: typeof ep.repeated_at === "string" ? ep.repeated_at : null,
      corrected: ep.corrected === true,
    });
  }
  return out.length > 0 ? out : null;
}

/** Russian skill names by key from the analysis (skills, errors) and the report's misconceptions. */
function skillNameMap(a: LessonAnalysis, report: Report | undefined): Map<string, string> {
  const names = new Map<string, string>();
  const misconceptions = report?.data.misconceptions;
  if (Array.isArray(misconceptions)) {
    for (const m of misconceptions) {
      if (typeof m === "object" && m !== null) {
        const r = m as Record<string, unknown>;
        if (typeof r.skill_key === "string" && typeof r.skill_name === "string") names.set(r.skill_key, r.skill_name);
      }
    }
  }
  for (const e of a.errors) names.set(e.skill_key, e.skill_name);
  for (const s of a.skills) names.set(s.skill_key, s.skill_name);
  return names;
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

  // The stored report is the source of truth for the episode chain; recompute only while it is missing.
  const episodes = episodesFromReport(report?.data) ?? episodesFromAnalysis(analysis);
  const skillNames = skillNameMap(analysis, report);
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
          episodes.map((ep, i) => (
            <article
              key={i}
              className={`episode${ep.corrected ? " corrected" : ""}${ep.repeatedAfterExplanation || ep.repeatedAt ? " repeated" : ""}`}
            >
              <div className="row">
                <Timecode seconds={ep.timestamp} />
                <strong>{ep.skillName}</strong>
              </div>
              <p className="mb-0 mt-2">{ep.description}</p>
              {ep.quote && <p className="quote">«{ep.quote}»</p>}
              {/* Three independent facts: when it happened, whether it was re-explained, whether it was fixed. */}
              <div className="episode-flow">
                <Badge tone="danger">Ошибка</Badge>
                {ep.repeatedAfterExplanation && <Badge tone="warning">после объяснения</Badge>}
                <span className="faint">→</span>
                {ep.reExplanation ? (
                  <Badge tone="info">Переобъяснение в {formatTimecode(ep.reExplanation.timestamp)}</Badge>
                ) : (
                  <Badge tone="neutral">Переобъяснения не было</Badge>
                )}
                {ep.repeatedAt && (
                  <>
                    <span className="faint">→</span>
                    <Badge tone="warning">Повторилась в {ep.repeatedAt}</Badge>
                  </>
                )}
                <span className="faint">→</span>
                {ep.corrected ? <Badge tone="success">Исправлена</Badge> : <Badge tone="neutral">Не исправлена</Badge>}
              </div>
              {ep.reExplanation?.summary && <p className="small muted mt-2 mb-0">{ep.reExplanation.summary}</p>}
              <FrameRefs frameIds={ep.frameIds} frames={frames} />
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
                  <strong>{skillNames.get(m.skill_key) ?? m.skill_key.replace(/_/g, " ")}</strong>
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
