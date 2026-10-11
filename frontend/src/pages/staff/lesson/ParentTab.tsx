/** "Для родителя": the parent report as the parent sees it, Telegram preview, send button with statuses. */
import { useState } from "react";
import { useSendParentReport } from "@/api/queries";
import { errorMessage } from "@/api/client";
import { ParentReportView } from "@/components/ParentReportView";
import { TelegramPreview } from "@/components/TelegramPreview";
import { useToast } from "@/components/Toast";
import { EmptyState, NotificationBadge, Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { CHANNEL_LABELS, NOTIFICATION_REASON_LABELS } from "@/lib/status";
import type { Frame, Lesson, Notification, ParentFrame, Report } from "@/types/api";
import type { LessonAnalysis } from "@/types/analysis";

/** Human text for a delivery row: the Russian `error` when present, else the reason slug translated. */
function describeNotification(n: Notification): string {
  if (n.error) return ` · ${n.error}`;
  const reason = typeof n.payload?.reason === "string" ? n.payload.reason : null;
  if (!reason) return "";
  return ` · ${NOTIFICATION_REASON_LABELS[reason] ?? "причина не указана"}`;
}

/**
 * The same frames the parent portal shows: the ones the analysis points at (errors, successes, visual issues),
 * in time order, with a plain caption — mirrors `select_parent_frames` in backend/app/api/parent.py.
 */
function selectParentFrames(analysis: LessonAnalysis | undefined, frames: Frame[]): ParentFrame[] {
  if (!analysis) return [];
  const byIndex = new Map(frames.map((f) => [f.index, f]));
  const captions = new Map<number, string>();
  const referenced = new Set<number>();
  const high: number[] = [];
  for (const v of analysis.visual_observations) {
    const text = (v.issue ?? v.description ?? "").trim();
    if (text && !captions.has(v.frame_id)) captions.set(v.frame_id, text);
    if (v.relevance === "high") high.push(v.frame_id);
    if (v.issue) referenced.add(v.frame_id);
  }
  for (const item of [...analysis.errors, ...analysis.independent_successes]) {
    for (const id of item.frame_ids) {
      referenced.add(id);
      if (item.description && !captions.has(id)) captions.set(id, item.description);
    }
  }
  const chosen = referenced.size > 0 ? referenced : new Set(high);
  return [...chosen]
    .map((id) => byIndex.get(id))
    .filter((f): f is Frame => Boolean(f))
    .sort((a, b) => a.timestamp_sec - b.timestamp_sec)
    .slice(0, 6)
    .map((f) => ({ index: f.index, timestamp_sec: f.timestamp_sec, url: f.url, caption: captions.get(f.index) ?? null }));
}

export function ParentTab({
  lesson,
  analysis,
  report,
  frames,
  loading,
  ready,
}: {
  lesson: Lesson;
  analysis: LessonAnalysis | undefined;
  report: Report | undefined;
  frames: Frame[];
  loading: boolean;
  ready: boolean;
}) {
  const toast = useToast();
  const send = useSendParentReport(lesson.id);
  const [result, setResult] = useState<Notification[] | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  if (!ready) {
    return <EmptyState title="Отчёт для родителя ещё не готов" text="Он появится автоматически после завершения анализа." />;
  }
  if (loading && !report && !analysis) return <Skeleton lines={8} />;

  // Prefer the stored report; fall back to the analysis' parent_report block.
  const data = report?.data ?? (analysis ? { ...analysis.parent_report, topic: analysis.topic } : null);
  const parentFrames = selectParentFrames(analysis, frames);

  const doSend = async () => {
    setSendError(null);
    try {
      const notifications = await send.mutateAsync();
      setResult(notifications);
      const sent = notifications.filter((n) => n.status === "sent").length;
      toast.show(sent > 0 ? `Отправлено: ${sent}` : "Отправка выполнена, смотрите статусы ниже.", sent > 0 ? "success" : "default");
    } catch (err) {
      setSendError(errorMessage(err, "Не удалось отправить отчёт."));
    }
  };

  return (
    <div className="parent-tab-layout">
      <div>
        <h3>Как увидит родитель</h3>
        <ParentReportView data={data} frames={parentFrames} teacherName={lesson.teacher_name} scheduledAt={lesson.scheduled_at} />
      </div>
      <div className="stack">
        <div className="card card-muted">
          <h4>Сообщение в Telegram</h4>
          <TelegramPreview html={report?.telegram_html} />
        </div>
        <div className="card card-muted">
          <h4>Отправка родителям</h4>
          <p className="small muted">
            Отчёт уйдёт всем родителям, привязанным к ученику, через выбранный ими канал, вместе со ссылкой в кабинет.
          </p>
          <button type="button" className="btn btn-primary btn-block" onClick={doSend} disabled={send.isPending || !report}>
            {send.isPending ? "Отправляем…" : "Отправить родителям"}
          </button>
          {!report && <p className="xs faint mt-2 mb-0">Кнопка станет доступна, когда отчёт будет сохранён.</p>}
          {sendError && <div className="form-error mt-2">{sendError}</div>}
          {result && (
            <div className="mt-4">
              {result.length === 0 ? (
                <p className="small muted mb-0">К ученику не привязан ни один родитель — отправлять некому. Добавьте родителя в разделе «Родители».</p>
              ) : (
                <ul className="evidence-list">
                  {result.map((n) => (
                    <li key={n.id}>
                      <div className="row row-between">
                        <span>{CHANNEL_LABELS[n.channel] ?? n.channel}</span>
                        <NotificationBadge status={n.status} />
                      </div>
                      <div className="xs muted">
                        {n.sent_at ? formatDateTime(n.sent_at) : formatDateTime(n.created_at)}
                        {describeNotification(n)}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
