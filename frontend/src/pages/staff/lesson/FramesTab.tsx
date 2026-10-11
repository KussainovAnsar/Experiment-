/** Keyframe grid; clicking a frame seeks the video. */
import type { UseQueryResult } from "@tanstack/react-query";
import { useMediaUrl } from "@/auth/media";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { useSeek } from "@/hooks/useSeek";
import { formatTimecode } from "@/lib/format";
import type { Frame } from "@/types/api";

const REASON_LABEL: Record<string, string> = { scene: "смена сцены", interval: "по интервалу" };

export function FramesTab({ query, activeIndex }: { query: UseQueryResult<Frame[]>; activeIndex: number | null }) {
  const seek = useSeek();
  const mediaUrl = useMediaUrl();
  if (query.isLoading) return <Skeleton lines={6} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  const frames = query.data ?? [];
  if (frames.length === 0) {
    return <EmptyState title="Кадры ещё не извлечены" text="Ключевые кадры появятся после этапа «Кадры». Для аудиозаписей кадров нет." />;
  }
  return (
    <div className="frames-grid">
      {frames.map((f) => (
        <button key={f.id} type="button" className={`frame-card${f.index === activeIndex ? " active" : ""}`} onClick={() => seek(f.timestamp_sec)} title="Перейти к моменту">
          <img src={mediaUrl(f.url)} alt={`Кадр ${f.index} на ${formatTimecode(f.timestamp_sec)}`} loading="lazy" />
          <div className="meta">
            <span>#{f.index} · {formatTimecode(f.timestamp_sec)}</span>
            <span>{REASON_LABEL[f.reason] ?? f.reason}</span>
          </div>
        </button>
      ))}
    </div>
  );
}
