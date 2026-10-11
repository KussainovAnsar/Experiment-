/**
 * Parent report rendered "as the parent sees it". Used by the staff lesson page (parent tab)
 * and by the parent portal. Data shape is produced by another module → rendered defensively.
 */
import { useMediaUrl } from "@/auth/media";
import { formatDateLong } from "@/lib/format";
import type { ParentFrame, ParentReportData } from "@/types/api";
import { FrameInline } from "./FrameThumb";
import { SkillChip, StringList } from "./ui";

function asStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

export function ParentReportView({
  data,
  frames,
  teacherName,
  scheduledAt,
}: {
  data: ParentReportData | Record<string, unknown> | null | undefined;
  /** Frames the report points at (errors, successes, visual issues) — already filtered by the API. */
  frames?: ParentFrame[];
  teacherName?: string;
  scheduledAt?: string;
}) {
  const mediaUrl = useMediaUrl();
  if (!data) {
    return <p className="muted">Отчёт для родителя ещё не сформирован.</p>;
  }
  const d = data as ParentReportData;
  const topic = asString(d.topic_plain) ?? asString(d.topic);
  const didWell = asStringArray(d.what_child_did_well);
  const wasHard = asStringArray(d.what_was_hard);
  const toRepeat = asStringArray(d.what_to_repeat);
  const homeTask = asString(d.home_task_suggestion);
  const encouragement = asString(d.encouragement);
  const progressNote = asString(d.progress_note);
  const childName = asString(d.child_first_name);
  const snapshot = (d.skills_snapshot ?? {}) as NonNullable<ParentReportData["skills_snapshot"]>;
  const snapStruggling = asStringArray(snapshot.struggling);
  const snapDeveloping = asStringArray(snapshot.developing);
  const snapMastered = asStringArray(snapshot.mastered);
  const hasSnapshot = snapStruggling.length + snapDeveloping.length + snapMastered.length > 0;
  const dateText = scheduledAt ? formatDateLong(scheduledAt) : asString(d.date);

  return (
    <div>
      <div className="section">
        <span className="xs faint">
          {dateText ? `Урок ${dateText}` : "Урок"}
          {teacherName ? ` · преподаватель ${teacherName}` : ""}
        </span>
        <h2 style={{ marginTop: 6 }}>{topic ?? "Тема урока"}</h2>
        {childName && <p className="muted mb-0">Ребёнок: {childName}</p>}
      </div>

      {didWell.length > 0 && (
        <div className="section section-well">
          <h3>Что получилось самостоятельно</h3>
          <StringList items={didWell} />
        </div>
      )}

      {wasHard.length > 0 && (
        <div className="section section-hard">
          <h3>Над чем ещё работаем</h3>
          <StringList items={wasHard} />
        </div>
      )}

      {(toRepeat.length > 0 || homeTask) && (
        <div className="section section-repeat">
          <h3>Что повторить дома</h3>
          <StringList items={toRepeat} />
          {homeTask && (
            <p className="mt-2 mb-0">
              <strong>Домашнее задание:</strong> {homeTask}
            </p>
          )}
        </div>
      )}

      {progressNote && (
        <div className="section">
          <h3>Как идёт прогресс</h3>
          <p className="mb-0">{progressNote}</p>
        </div>
      )}

      {hasSnapshot && (
        <div className="section">
          <h3>Навыки на сегодня</h3>
          <div className="chips">
            {snapMastered.map((s) => (
              <SkillChip key={`m-${s}`} status="mastered">
                {s}
              </SkillChip>
            ))}
            {snapDeveloping.map((s) => (
              <SkillChip key={`d-${s}`} status="developing">
                {s}
              </SkillChip>
            ))}
            {snapStruggling.map((s) => (
              <SkillChip key={`s-${s}`} status="struggling">
                {s}
              </SkillChip>
            ))}
          </div>
          <p className="xs faint mt-2 mb-0">
            Зелёное — ребёнок справляется сам, жёлтое — нужна практика, красное — стоит повторить дома.
          </p>
        </div>
      )}

      {frames && frames.length > 0 && (
        <div className="section">
          <h3>Моменты урока</h3>
          <p className="xs faint mt-0">Кадры экрана, о которых идёт речь в отчёте. Нажмите, чтобы открыть крупнее.</p>
          <div className="row parent-moments">
            {frames.slice(0, 6).map((f) => (
              <FrameInline
                key={f.index}
                frame={f}
                caption={f.caption ?? "Момент урока"}
                onClick={() => window.open(mediaUrl(f.url), "_blank", "noopener")}
              />
            ))}
          </div>
        </div>
      )}

      {encouragement && <div className="section section-encourage">{encouragement}</div>}
    </div>
  );
}
