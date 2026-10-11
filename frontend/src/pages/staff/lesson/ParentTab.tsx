/** "Для родителя": the parent report as the parent sees it, Telegram preview, send button with statuses. */
import { useState } from "react";
import { useSendParentReport } from "@/api/queries";
import { errorMessage } from "@/api/client";
import { ParentReportView } from "@/components/ParentReportView";
import { TelegramPreview } from "@/components/TelegramPreview";
import { useToast } from "@/components/Toast";
import { EmptyState, NotificationBadge, Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { CHANNEL_LABELS } from "@/lib/status";
import type { Frame, Lesson, Notification, Report } from "@/types/api";
import type { LessonAnalysis } from "@/types/analysis";

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
  const parentFrames = frames.map((f) => ({ index: f.index, timestamp_sec: f.timestamp_sec, url: f.url }));

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
    <div className="lesson-layout" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(280px, 360px)" }}>
      <div>
        <h3>Как увидит родитель</h3>
        <ParentReportView data={data} frames={parentFrames} realm="staff" teacherName={lesson.teacher_name} scheduledAt={lesson.scheduled_at} />
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
                        {n.error ? ` · ${n.error}` : ""}
                        {n.status === "skipped" && typeof n.payload?.reason === "string" ? ` · ${n.payload.reason}` : ""}
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
