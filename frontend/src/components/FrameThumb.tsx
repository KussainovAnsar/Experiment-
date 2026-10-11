/** Frame thumbnails: media URLs carry the short-lived media token from `MediaTokenContext`. */
import { useMediaUrl } from "@/auth/media";
import { formatTimecode } from "@/lib/format";
import { useSeek } from "@/hooks/useSeek";
import type { Frame, ParentFrame } from "@/types/api";

type AnyFrame = Frame | ParentFrame;

/** Pick the frame whose timestamp is closest to `seconds` (null when no frames). */
export function nearestFrame<T extends AnyFrame>(frames: T[] | undefined, seconds: number | null): T | null {
  if (!frames || frames.length === 0 || seconds === null) return null;
  let best: T | null = null;
  let bestDist = Infinity;
  for (const f of frames) {
    const d = Math.abs(f.timestamp_sec - seconds);
    if (d < bestDist) {
      best = f;
      bestDist = d;
    }
  }
  return best;
}

export function FrameInline({
  frame,
  caption,
  onClick,
}: {
  frame: AnyFrame;
  /** Replaces the technical «#index · mm:ss» label (parent portal). */
  caption?: string | null;
  onClick?: () => void;
}) {
  const seek = useSeek();
  const mediaUrl = useMediaUrl();
  const technical = `Кадр ${frame.index} · ${formatTimecode(frame.timestamp_sec)}`;
  return (
    <button
      type="button"
      className={`frame-inline${caption ? " frame-inline-captioned" : ""}`}
      onClick={onClick ?? (() => seek(frame.timestamp_sec))}
      title={caption ?? technical}
    >
      <img src={mediaUrl(frame.url)} alt={caption ?? technical} loading="lazy" />
      <span>{caption ?? `#${frame.index} · ${formatTimecode(frame.timestamp_sec)}`}</span>
    </button>
  );
}

/** Render inline thumbnails for a list of frame indices (unknown indices are skipped). */
export function FrameRefs({ frameIds, frames }: { frameIds: number[]; frames?: AnyFrame[] }) {
  if (!frames || frameIds.length === 0) return null;
  const byIndex = new Map(frames.map((f) => [f.index, f]));
  const found = frameIds.map((id) => byIndex.get(id)).filter((f): f is AnyFrame => Boolean(f));
  if (found.length === 0) return null;
  return (
    <div className="row mt-2">
      {found.map((f) => (
        <FrameInline key={f.index} frame={f} />
      ))}
    </div>
  );
}
