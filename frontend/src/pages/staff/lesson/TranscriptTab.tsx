/** Transcript with speaker colours and clickable timecodes; current segment follows the video. */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Timecode } from "@/components/Timecode";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui";
import type { Transcript } from "@/types/api";

const SPEAKER_LABEL: Record<string, string> = {
  teacher: "Учитель",
  student: "Ученик",
  unknown: "—",
};

function speakerClass(speaker: string): string {
  if (speaker === "teacher" || speaker === "student") return `speaker speaker-${speaker}`;
  return "speaker speaker-unknown";
}

export function TranscriptTab({ query, hasTranscript, position }: { query: UseQueryResult<Transcript>; hasTranscript: boolean; position: number | null }) {
  const [filter, setFilter] = useState("");
  const segments = query.data?.segments ?? [];
  const activeIndex = useMemo(() => {
    if (position === null) return -1;
    return segments.findIndex((s) => position >= s.start && position < s.end);
  }, [segments, position]);
  const visible = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return segments
      .map((s, i) => ({ s, i }))
      .filter(({ s }) => !f || s.text.toLowerCase().includes(f));
  }, [segments, filter]);

  if (!hasTranscript) {
    return <EmptyState title="Транскрипт ещё не готов" text="Расшифровка речи появится после этапа «Расшифровка»." />;
  }
  if (query.isLoading) return <Skeleton lines={10} />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  if (!query.data || segments.length === 0) {
    return <EmptyState title="В транскрипте нет реплик" text="Возможно, на записи не удалось распознать речь." />;
  }

  return (
    <div>
      <div className="row row-between" style={{ marginBottom: 12 }}>
        <span className="small muted">
          Провайдер: {query.data.provider} · язык: {query.data.language ?? "—"} · реплик: {segments.length}
          {query.data.word_count ? ` · слов: ${query.data.word_count}` : ""}
        </span>
        <input className="input" style={{ maxWidth: 260 }} placeholder="Поиск по тексту…" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Поиск по транскрипту" />
      </div>
      <div className="transcript">
        {visible.length === 0 && <p className="muted small">Ничего не найдено.</p>}
        {visible.map(({ s, i }) => (
          <div key={i} className={`segment${i === activeIndex ? " active" : ""}`}>
            <Timecode seconds={s.start} />
            <span className={speakerClass(s.speaker)}>{SPEAKER_LABEL[s.speaker] ?? s.speaker}</span>
            <span className="text">{s.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
