/** Small presentational building blocks shared by staff and parent pages. */
import { useEffect, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { errorMessage } from "@/api/client";
import { lessonProgress, LESSON_STATUS_META, NOTIFICATION_STATUS_META, SKILL_STATUS_META, type BadgeTone } from "@/lib/status";
import type { LessonStatus, NotificationStatus } from "@/types/api";
import type { SkillStatus } from "@/types/analysis";

export function Badge({ tone = "neutral", children, pulse }: { tone?: BadgeTone; children: ReactNode; pulse?: boolean }) {
  return (
    <span className={`badge badge-${tone}`}>
      {pulse !== undefined && <span className={`badge-dot${pulse ? " pulse" : ""}`} />}
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: LessonStatus }) {
  const meta = LESSON_STATUS_META[status] ?? { label: status, tone: "neutral" as BadgeTone, active: false };
  return (
    <Badge tone={meta.tone} pulse={meta.active}>
      {meta.label}
    </Badge>
  );
}

export function NotificationBadge({ status }: { status: NotificationStatus }) {
  const meta = NOTIFICATION_STATUS_META[status] ?? { label: status, tone: "neutral" as BadgeTone };
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function SkillChip({ status, children, title }: { status: SkillStatus; children: ReactNode; title?: string }) {
  return (
    <span className={`chip chip-${status}`} title={title ?? SKILL_STATUS_META[status]?.hint}>
      {children}
    </span>
  );
}

export function SkillStatusBadge({ status }: { status: SkillStatus }) {
  const meta = SKILL_STATUS_META[status] ?? { label: status, tone: "neutral" as BadgeTone };
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}

export function ProgressBar({ value, indeterminate }: { value: number; indeterminate?: boolean }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <div className={`progress-bar${indeterminate ? " indeterminate" : ""}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Compact processing progress for list rows: label + bar from stage_index/stage_total. */
export function LessonProgressInline({
  status,
  stageIndex,
  stageTotal,
  message,
}: {
  status: LessonStatus;
  stageIndex: number | null;
  stageTotal: number;
  message?: string | null;
}) {
  const meta = LESSON_STATUS_META[status];
  if (!meta?.active) return null;
  const value = lessonProgress(stageIndex, stageTotal, status);
  return (
    <div className="stack" style={{ gap: 4, minWidth: 160 }}>
      <ProgressBar value={value} indeterminate={value === 0} />
      <span className="xs muted">
        {meta.label}
        {message ? ` — ${message}` : ""}
      </span>
    </div>
  );
}

export function Skeleton({ lines = 3, height }: { lines?: number; height?: number }) {
  return (
    <div className="skeleton-lines" aria-busy="true" aria-live="polite">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ height: height ?? 14, width: `${100 - (i % 3) * 12}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ title, text, action }: { title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <div className="error-box" role="alert">
      <span>
        {title ? <strong>{title}: </strong> : null}
        {errorMessage(error)}
      </span>
      {onRetry && (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warning" | "success"; children: ReactNode }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "warn" | "danger";
}) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function CopyBox({ value }: { value: string }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      /* clipboard may be unavailable; the input stays selectable */
    }
  };
  return (
    <div className="copy-box">
      <input className="input" readOnly value={value} onFocus={(e) => e.currentTarget.select()} />
      <button type="button" className="btn btn-sm" onClick={copy}>
        Копировать
      </button>
    </div>
  );
}

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="small muted">
      ← {children}
    </Link>
  );
}

/** Render a list of strings; returns null when empty. */
export function StringList({ items }: { items: unknown }) {
  if (!Array.isArray(items) || items.length === 0) return null;
  return (
    <ul>
      {items.map((it, i) => (
        <li key={i}>{typeof it === "string" ? it : JSON.stringify(it)}</li>
      ))}
    </ul>
  );
}
