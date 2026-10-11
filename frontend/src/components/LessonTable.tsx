/** Reusable lessons table used by the dashboard, lessons list and student detail. */
import { Link } from "react-router-dom";
import { formatDateTime, formatDuration } from "@/lib/format";
import type { Lesson } from "@/types/api";
import { LessonProgressInline, StatusBadge } from "./ui";

export function LessonTable({ lessons, hideStudent, hideTeacher }: { lessons: Lesson[]; hideStudent?: boolean; hideTeacher?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Дата урока</th>
            {!hideStudent && <th>Ученик</th>}
            {!hideTeacher && <th>Преподаватель</th>}
            <th>Длительность</th>
            <th>Статус</th>
            <th>Отчёты</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lessons.map((l) => (
            <tr key={l.id}>
              <td className="nowrap">
                <Link to={`/lessons/${l.id}`}>{formatDateTime(l.scheduled_at)}</Link>
              </td>
              {!hideStudent && (
                <td>
                  <Link to={`/students/${l.student_id}`}>{l.student_name}</Link>
                </td>
              )}
              {!hideTeacher && <td>{l.teacher_name}</td>}
              <td className="nowrap">{formatDuration(l.duration_sec)}</td>
              <td>
                <div className="stack" style={{ gap: 6 }}>
                  <StatusBadge status={l.status} />
                  <LessonProgressInline
                    status={l.status}
                    stageIndex={l.stage_index}
                    stageTotal={l.stage_total}
                    message={l.status_message}
                  />
                  {l.status === "failed" && l.error && (
                    <span className="xs priority-high" title={l.error}>
                      {l.error.length > 80 ? `${l.error.slice(0, 80)}…` : l.error}
                    </span>
                  )}
                </div>
              </td>
              <td className="small muted">
                {l.report_kinds.length === 0
                  ? "—"
                  : l.report_kinds
                      .map((k) => ({ teacher: "учителю", manager: "методисту", parent: "родителю" })[k] ?? k)
                      .join(", ")}
              </td>
              <td className="nowrap">
                <Link to={`/lessons/${l.id}`} className="btn btn-sm">
                  Открыть
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
