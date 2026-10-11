/** Frame thumbnails that resolve frame indices to media URLs with the access token. */
import { withAccessToken, type Realm } from "@/auth/storage";
import { formatTimecode } from "@/lib/format";
import { useSeek } from "@/hooks/useSeek";
import type { Frame, ParentFrame } from "@/types/api";

type AnyFrame = Frame | ParentFrame;

export function frameSrc(frame: AnyFrame, realm: Realm): string {
  return withAccessToken(frame.url, realm);
}

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
  realm = "staff",
  onClick,
}: {
  frame: AnyFrame;
  realm?: Realm;
  onClick?: () => void;
}) {
  const seek = useSeek();
  return (
    <button
      type="button"
      className="frame-inline"
      onClick={onClick ?? (() => seek(frame.timestamp_sec))}
      title={`Кадр ${frame.index} · ${formatTimecode(frame.timestamp_sec)}`}
    >
      <img src={frameSrc(frame, realm)} alt={`Кадр ${frame.index}`} loading="lazy" />
      <span>
        #{frame.index} · {formatTimecode(frame.timestamp_sec)}
      </span>
    </button>
  );
}

/** Render inline thumbnails for a list of frame indices (unknown indices are skipped). */
export function FrameRefs({ frameIds, frames, realm = "staff" }: { frameIds: number[]; frames?: AnyFrame[]; realm?: Realm }) {
  if (!frames || frameIds.length === 0) return null;
  const byIndex = new Map(frames.map((f) => [f.index, f]));
  const found = frameIds.map((id) => byIndex.get(id)).filter((f): f is AnyFrame => Boolean(f));
  if (found.length === 0) return null;
  return (
    <div className="row mt-2">
      {found.map((f) => (
        <FrameInline key={f.index} frame={f} realm={realm} />
      ))}
    </div>
  );
}
