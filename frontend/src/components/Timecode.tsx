/** Clickable timecode button: seeks the lesson video and highlights the nearest frame. */
import { formatTimecode } from "@/lib/format";
import { useSeek } from "@/hooks/useSeek";

export function Timecode({ seconds, label }: { seconds: number | null | undefined; label?: string }) {
  const seek = useSeek();
  if (seconds === null || seconds === undefined) return null;
  return (
    <button
      type="button"
      className="timecode"
      title="Перейти к этому моменту записи"
      onClick={() => seek(seconds)}
    >
      ▶ {label ?? formatTimecode(seconds)}
    </button>
  );
}
