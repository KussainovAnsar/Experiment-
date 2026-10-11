/** Formatting helpers: timecodes, dates, durations, percentages (Russian locale). */

/** Format seconds as mm:ss (or h:mm:ss when ≥ 1 hour). Negative/NaN → "00:00". */
export function formatTimecode(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return "00:00";
  }
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Human-readable duration: "1 ч 05 мин", "12 мин", "45 с". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return "—";
  }
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  if (h > 0) return `${h} ч ${String(m).padStart(2, "0")} мин`;
  if (m > 0) return `${m} мин`;
  return `${total} с`;
}

const dateTimeFmt = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const dateFmt = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

const shortDateFmt = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "11.10.2026, 14:30" */
export function formatDateTime(iso: string | null | undefined): string {
  const d = parseDate(iso);
  return d ? dateTimeFmt.format(d) : "—";
}

/** "11 октября 2026 г." */
export function formatDateLong(iso: string | null | undefined): string {
  const d = parseDate(iso);
  return d ? dateFmt.format(d) : "—";
}

/** "11.10.2026" */
export function formatDateShort(iso: string | null | undefined): string {
  const d = parseDate(iso);
  return d ? shortDateFmt.format(d) : "—";
}

/** 0.42 → "42 %" ; null → "—". */
export function formatPercent(share: number | null | undefined): string {
  if (share === null || share === undefined || !Number.isFinite(share)) return "—";
  return `${Math.round(share * 100)} %`;
}

/** Russian plural helper: pluralize(3, ["урок", "урока", "уроков"]) → "3 урока". */
export function pluralize(n: number, forms: [string, string, string], withNumber = true): string {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  let form: string;
  if (abs > 10 && abs < 20) form = forms[2];
  else if (last > 1 && last < 5) form = forms[1];
  else if (last === 1) form = forms[0];
  else form = forms[2];
  return withNumber ? `${n} ${form}` : form;
}

/** Convert a Date to the value expected by <input type="datetime-local"> (local time, no seconds). */
export function toDatetimeLocalValue(d: Date): string {
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Convert a datetime-local input value to an ISO 8601 string with timezone offset. */
export function datetimeLocalToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

/** Format bytes: "1,2 ГБ". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1).replace(".", ",")} ${units[i]}`;
}
