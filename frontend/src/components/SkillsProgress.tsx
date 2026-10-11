/** Skills grouped by status with chips + per-skill evidence; lesson dynamics. Shared by staff and parent pages. */
import { Link } from "react-router-dom";
import { formatDateShort, formatTimecode, pluralize } from "@/lib/format";
import { SKILL_STATUS_META } from "@/lib/status";
import type { LessonProgressPoint, SkillProgress, SkillsCounts } from "@/types/api";
import type { SkillStatus } from "@/types/analysis";
import { SkillChip, SkillStatusBadge } from "./ui";

const ORDER: SkillStatus[] = ["struggling", "developing", "mastered"];

export function SkillCounts({ counts }: { counts: SkillsCounts }) {
  return (
    <div className="counts">
      {ORDER.map((st) => (
        <span key={st} className={`count-pill chip-${st}`}>
          <b>{counts[st] ?? 0}</b> {SKILL_STATUS_META[st]!.label.toLowerCase()}
        </span>
      ))}
    </div>
  );
}

export function SkillLegend() {
  return (
    <div className="stack" style={{ gap: 6 }}>
      {ORDER.map((st) => (
        <div key={st} className="row small">
          <SkillStatusBadge status={st} />
          <span className="muted">{SKILL_STATUS_META[st]!.hint}</span>
        </div>
      ))}
    </div>
  );
}

export function SkillGroups({
  skills,
  lessonLink,
  compact,
}: {
  skills: SkillProgress[];
  /** Builds a link to a lesson (staff: /lessons/:id, parent: /parent/students/:sid/lessons/:id). */
  lessonLink: (lessonId: string) => string;
  compact?: boolean;
}) {
  if (skills.length === 0) {
    return <p className="muted">Пока нет данных о навыках — они появятся после первого обработанного урока.</p>;
  }
  return (
    <div className="stack" style={{ gap: "var(--space-4)" }}>
      {ORDER.map((status) => {
        const group = skills.filter((s) => s.status === status);
        if (group.length === 0) return null;
        return (
          <section key={status}>
            <div className="row" style={{ marginBottom: 8 }}>
              <SkillStatusBadge status={status} />
              <span className="small muted">{pluralize(group.length, ["навык", "навыка", "навыков"])}</span>
            </div>
            <div className="chips" style={{ marginBottom: 10 }}>
              {group.map((s) => (
                <SkillChip key={s.skill_key} status={status}>
                  {s.skill_name}
                </SkillChip>
              ))}
            </div>
            {!compact &&
              group.map((s) => (
                <details key={s.skill_key} className="skill">
                  <summary>
                    <span>{s.skill_name}</span>
                    <span className="xs muted nowrap">
                      встречался {pluralize(s.times_seen, ["раз", "раза", "раз"])}
                      {s.times_struggled > 0 ? ` · с трудностями ${pluralize(s.times_struggled, ["раз", "раза", "раз"])}` : ""}
                    </span>
                  </summary>
                  {s.evidence.length === 0 ? (
                    <p className="small muted mb-0">Подробностей пока нет.</p>
                  ) : (
                    <ul className="evidence-list">
                      {s.evidence.map((e, i) => (
                        <li key={i}>
                          <Link to={lessonLink(e.lesson_id)}>
                            Урок{e.timestamp_sec !== null && e.timestamp_sec !== undefined ? ` · ${formatTimecode(e.timestamp_sec)}` : ""}
                          </Link>
                          {e.status && (
                            <>
                              {" "}
                              <SkillStatusBadge status={e.status} />
                            </>
                          )}
                          <div>{e.note}</div>
                        </li>
                      ))}
                    </ul>
                  )}
                  {s.last_seen_lesson_id && (
                    <p className="xs faint mt-2 mb-0">
                      <Link to={lessonLink(s.last_seen_lesson_id)}>Последний урок с этим навыком →</Link>
                    </p>
                  )}
                </details>
              ))}
          </section>
        );
      })}
    </div>
  );
}

/** Chronological lesson dynamics: errors vs independent successes. */
export function LessonDynamics({ lessons, lessonLink }: { lessons: LessonProgressPoint[]; lessonLink: (lessonId: string) => string }) {
  if (lessons.length === 0) return <p className="muted">Обработанных уроков пока нет.</p>;
  const max = Math.max(1, ...lessons.map((l) => Math.max(l.errors_count, l.independent_successes_count)));
  return (
    <div>
      <div className="mini-bars" role="img" aria-label="Динамика по урокам: ошибки и самостоятельные успехи">
        {lessons.map((l) => (
          <Link key={l.lesson_id} to={lessonLink(l.lesson_id)} className="mini-bar" title={`${formatDateShort(l.scheduled_at)}: ошибок ${l.errors_count}, успехов ${l.independent_successes_count}`}>
            <div className="bars">
              <span className="b-err" style={{ height: `${(l.errors_count / max) * 100}%` }} />
              <span className="b-ok" style={{ height: `${(l.independent_successes_count / max) * 100}%` }} />
            </div>
            <span className="lbl">{formatDateShort(l.scheduled_at).slice(0, 5)}</span>
          </Link>
        ))}
      </div>
      <div className="legend mt-2">
        <span>
          <i className="legend-dot" style={{ background: "var(--color-danger)" }} />
          Ошибки
        </span>
        <span>
          <i className="legend-dot" style={{ background: "var(--color-success)" }} />
          Самостоятельные успехи
        </span>
      </div>
    </div>
  );
}

export function LessonDynamicsTable({ lessons, lessonLink }: { lessons: LessonProgressPoint[]; lessonLink: (lessonId: string) => string }) {
  if (lessons.length === 0) return null;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Дата</th>
            <th>Тема</th>
            <th className="num">Ошибок</th>
            <th className="num">Успехов</th>
            <th className="num">Заданий решено сам.</th>
          </tr>
        </thead>
        <tbody>
          {lessons.map((l) => (
            <tr key={l.lesson_id}>
              <td className="nowrap">
                <Link to={lessonLink(l.lesson_id)}>{formatDateShort(l.scheduled_at)}</Link>
              </td>
              <td>{l.topic ?? <span className="faint">—</span>}</td>
              <td className="num">{l.errors_count}</td>
              <td className="num">{l.independent_successes_count}</td>
              <td className="num">
                {l.tasks_solved_independently} / {l.tasks_total}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
