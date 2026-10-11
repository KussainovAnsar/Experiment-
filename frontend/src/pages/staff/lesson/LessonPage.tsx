import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  useAnalysis,
  useFrames,
  useLesson,
  useLessonMediaToken,
  useReports,
  useReprocessLesson,
  useTranscript,
} from "@/api/queries";
import { lessonsApi } from "@/api/staff";
import { useStaffAuth } from "@/auth/StaffAuth";
import { MediaTokenContext } from "@/auth/media";
import { withMediaToken } from "@/auth/storage";
import { nearestFrame } from "@/components/FrameThumb";
import { StatusTimeline } from "@/components/StatusTimeline";
import { Tabs, type TabItem } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { BackLink, ErrorState, Notice, PageHeader, Skeleton, StatusBadge } from "@/components/ui";
import { SeekContext, type SeekContextValue } from "@/hooks/useSeek";
import { formatDateTime, formatDuration, formatElapsed, formatTimecode } from "@/lib/format";
import type { Frame, Report, ReportKind } from "@/types/api";
import { FramesTab } from "./FramesTab";
import { ManagerTab } from "./ManagerTab";
import { ParentTab } from "./ParentTab";
import { TeacherTab } from "./TeacherTab";
import { TranscriptTab } from "./TranscriptTab";

type TabKey = "teacher" | "manager" | "parent" | "transcript" | "frames";

function pickReport(reports: Report[] | undefined, kind: ReportKind): Report | undefined {
  if (!reports) return undefined;
  return reports.filter((r) => r.kind === kind).sort((a, b) => b.version - a.version)[0];
}

export function LessonPage() {
  const { id = "" } = useParams();
  const { canSeeManagerReport } = useStaffAuth();
  const toast = useToast();
  const [sp, setSp] = useSearchParams();
  const lessonQ = useLesson(id);
  const lesson = lessonQ.data;
  const ready = Boolean(lesson && (lesson.status === "done" || lesson.has_analysis));
  const hasTranscript = Boolean(lesson?.has_transcript);
  const transcriptQ = useTranscript(id, hasTranscript);
  // Frames exist once the extracting_frames stage (index 4) is finished → from analyzing (5) onwards.
  const framesReady = Boolean(
    lesson && ((lesson.stage_index ?? 0) >= 5 || lesson.status === "done" || lesson.status === "failed"),
  );
  const framesQ = useFrames(id, framesReady);
  // Short-lived, lesson-scoped token for <img>/<video> URLs (never the session JWT; see auth/media.tsx).
  const mediaTokenQ = useLessonMediaToken(id, Boolean(lesson));
  const mediaToken = mediaTokenQ.data?.access_token ?? null;
  const analysisQ = useAnalysis(id, ready);
  const reportsQ = useReports(id, ready && (lesson?.report_kinds.length ?? 0) > 0);
  const reprocess = useReprocessLesson(id);
  const qc = useQueryClient();

  // When processing finishes (or fails), refresh every sub-resource so tabs show final data.
  const prevStatus = useRef(lesson?.status);
  useEffect(() => {
    if (lesson && prevStatus.current !== lesson.status) {
      prevStatus.current = lesson.status;
      if (lesson.status === "done" || lesson.status === "failed") {
        void qc.invalidateQueries({ queryKey: ["lesson", id] });
      }
    }
  }, [lesson, id, qc]);

  const requestedTab = sp.get("tab") as TabKey | null;
  const defaultTab: TabKey = "teacher";
  const tab: TabKey = requestedTab && (requestedTab !== "manager" || canSeeManagerReport) ? requestedTab : defaultTab;
  const setTab = (key: TabKey) => {
    const next = new URLSearchParams(sp);
    next.set("tab", key);
    setSp(next, { replace: true });
  };

  // ---------------------------------------------------------------- video + seek context
  const videoRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const attachMedia = useCallback((el: HTMLVideoElement | HTMLAudioElement | null) => {
    videoRef.current = el;
  }, []);
  const [position, setPosition] = useState<number | null>(null);
  const [videoFailed, setVideoFailed] = useState(false);
  const seek = useCallback((seconds: number) => {
    setPosition(seconds);
    const v = videoRef.current;
    if (v) {
      try {
        v.currentTime = Math.max(0, seconds);
        const playing = v.play() as Promise<void> | undefined;
        if (playing && typeof playing.catch === "function") playing.catch(() => undefined);
      } catch {
        /* media not loaded yet */
      }
      v.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, []);
  const seekValue = useMemo<SeekContextValue>(() => ({ seek, position }), [seek, position]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return undefined;
    let last = 0;
    const onTime = () => {
      const now = Date.now();
      if (now - last < 500) return;
      last = now;
      setPosition(v.currentTime);
    };
    v.addEventListener("timeupdate", onTime);
    return () => v.removeEventListener("timeupdate", onTime);
  }, [lesson?.id, lesson?.media_kind, videoFailed]);

  const frames: Frame[] = framesQ.data ?? [];
  const activeFrame = nearestFrame(frames, position);
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!activeFrame || !stripRef.current) return;
    const el = stripRef.current.querySelector<HTMLElement>(`[data-frame="${activeFrame.index}"]`);
    el?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [activeFrame?.index]);

  const sourceUrl = withMediaToken(lessonsApi.sourceUrl(id), mediaToken);

  if (lessonQ.isLoading) {
    return (
      <div className="card">
        <Skeleton lines={6} />
      </div>
    );
  }
  if (lessonQ.isError || !lesson) {
    return (
      <>
        <BackLink to="/lessons">К списку уроков</BackLink>
        <div className="mt-4">
          <ErrorState error={lessonQ.error ?? new Error("Урок не найден")} onRetry={() => void lessonQ.refetch()} />
        </div>
      </>
    );
  }

  const analysis = analysisQ.data?.data;
  const teacherReport = pickReport(reportsQ.data, "teacher");
  const managerReport = pickReport(reportsQ.data, "manager");
  const parentReport = pickReport(reportsQ.data, "parent");

  const tabs: TabItem<TabKey>[] = [
    { key: "teacher", label: "Для учителя" },
    ...(canSeeManagerReport ? [{ key: "manager" as TabKey, label: "Для методиста" }] : []),
    { key: "parent", label: "Для родителя" },
    { key: "transcript", label: "Транскрипт", count: transcriptQ.data?.segments.length },
    { key: "frames", label: "Кадры", count: frames.length || undefined },
  ];

  const doReprocess = async () => {
    try {
      await reprocess.mutateAsync();
      toast.show("Обработка запущена повторно.", "success");
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "Не удалось запустить обработку.", "error");
    }
  };

  return (
    <SeekContext.Provider value={seekValue}>
      <MediaTokenContext.Provider value={mediaToken}>
      <BackLink to="/lessons">К списку уроков</BackLink>
      <PageHeader
        title={
          <span className="row">
            Урок {formatDateTime(lesson.scheduled_at)} <StatusBadge status={lesson.status} />
          </span>
        }
        subtitle={
          <>
            Ученик: <Link to={`/students/${lesson.student_id}`}>{lesson.student_name}</Link> · Преподаватель: {lesson.teacher_name} ·
            Длительность: {formatDuration(lesson.duration_sec)}
            {analysis?.topic ? ` · Тема: ${analysis.topic}` : ""}
            {lesson.status === "done" && lesson.processing_stats?.total_sec != null
              ? ` · Обработано за ${formatElapsed(lesson.processing_stats.total_sec)}`
              : ""}
          </>
        }
        actions={
          (lesson.status === "failed" || lesson.status === "done") && (
            <button type="button" className="btn" onClick={doReprocess} disabled={reprocess.isPending}>
              {reprocess.isPending ? "Запускаем…" : "Обработать заново"}
            </button>
          )
        }
      />

      <StatusTimeline lesson={lesson} />

      <div className="lesson-layout mt-4">
        <div>
          {!ready && lesson.status !== "failed" && (
            <Notice tone="info">
              Отчёты появятся, когда обработка завершится. Пока доступны{hasTranscript ? " транскрипт и" : ""} кадры по мере готовности.
            </Notice>
          )}
          {lesson.status === "failed" && !ready && (
            <Notice tone="warning">
              Обработка не завершилась. Проверьте файл записи и запустите обработку заново — при повторной ошибке
              обратитесь к администратору.
            </Notice>
          )}
          <div className="card mt-4">
            <Tabs tabs={tabs} active={tab} onChange={setTab} />
            {tab === "teacher" && (
              <TeacherTab analysis={analysis} report={teacherReport} frames={frames} loading={analysisQ.isLoading || reportsQ.isLoading} ready={ready} />
            )}
            {tab === "manager" && canSeeManagerReport && (
              <ManagerTab analysis={analysis} report={managerReport} loading={analysisQ.isLoading || reportsQ.isLoading} ready={ready} />
            )}
            {tab === "parent" && (
              <ParentTab lesson={lesson} analysis={analysis} report={parentReport} frames={frames} loading={analysisQ.isLoading || reportsQ.isLoading} ready={ready} />
            )}
            {tab === "transcript" && (
              <TranscriptTab query={transcriptQ} hasTranscript={hasTranscript} position={position} />
            )}
            {tab === "frames" && <FramesTab query={framesQ} activeIndex={activeFrame?.index ?? null} />}
          </div>
        </div>

        <aside>
          <div className="video-wrap">
            {!mediaToken ? (
              <div className="video-placeholder">Запись загружается…</div>
            ) : videoFailed ? (
              <div className="video-placeholder">
                Запись недоступна для просмотра в браузере.
                <br />
                {/* the link carries only the 10-minute lesson-scoped media token, never the session JWT */}
                <a href={sourceUrl} target="_blank" rel="noreferrer" style={{ color: "#9db7ff" }}>
                  Скачать исходный файл
                </a>
              </div>
            ) : lesson.media_kind === "audio" ? (
              <audio ref={attachMedia} controls preload="metadata" src={sourceUrl} style={{ width: "100%", padding: 12 }} onError={() => setVideoFailed(true)} />
            ) : (
              <video ref={attachMedia} controls preload="metadata" src={sourceUrl} onError={() => setVideoFailed(true)} />
            )}
            {frames.length > 0 && (
              <div className="frame-strip" ref={stripRef} aria-label="Ключевые кадры">
                {frames.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    data-frame={f.index}
                    className={`frame-thumb${activeFrame?.index === f.index ? " active" : ""}`}
                    onClick={() => seek(f.timestamp_sec)}
                    title={`Кадр ${f.index} · ${formatTimecode(f.timestamp_sec)}`}
                  >
                    <img src={withMediaToken(f.url, mediaToken)} alt={`Кадр ${f.index}`} loading="lazy" />
                    <span>{formatTimecode(f.timestamp_sec)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          {position !== null && (
            <p className="xs muted mt-2">
              Позиция: {formatTimecode(position)}
              {activeFrame ? ` · ближайший кадр #${activeFrame.index} (${formatTimecode(activeFrame.timestamp_sec)})` : ""}
            </p>
          )}
        </aside>
      </div>
      </MediaTokenContext.Provider>
    </SeekContext.Provider>
  );
}
