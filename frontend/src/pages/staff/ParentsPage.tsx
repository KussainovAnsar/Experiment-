import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { errorMessage } from "@/api/client";
import { keys, useParents, useStudents } from "@/api/queries";
import { parentsApi } from "@/api/staff";
import { useToast } from "@/components/Toast";
import { Badge, CopyBox, EmptyState, ErrorState, Modal, NotificationBadge, PageHeader, Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import { CHANNEL_LABELS, LANGUAGE_LABELS } from "@/lib/status";
import type { Channel, Language, Notification, Parent } from "@/types/api";

interface MagicState {
  parent: Parent;
  url: string;
}

interface InviteState {
  parent: Parent;
  notification: Notification;
}

export function ParentsPage() {
  const q = useParents();
  const students = useStudents();
  const qc = useQueryClient();
  const toast = useToast();

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    full_name: "",
    phone: "",
    email: "",
    preferred_channel: "telegram" as Channel,
    preferred_language: "ru" as Language,
    student_id: "",
    relation: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [magic, setMagic] = useState<MagicState | null>(null);
  const [invite, setInvite] = useState<InviteState | null>(null);
  const [linkFor, setLinkFor] = useState<Parent | null>(null);
  const [linkStudent, setLinkStudent] = useState({ student_id: "", relation: "" });
  const [pending, setPending] = useState<string | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: keys.parents });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await parentsApi.create({
        full_name: form.full_name.trim(),
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
        preferred_channel: form.preferred_channel,
        preferred_language: form.preferred_language,
        student_ids: form.student_id ? [form.student_id] : [],
        relation: form.relation.trim() || null,
      });
      await refresh();
      setCreateOpen(false);
      setForm({ full_name: "", phone: "", email: "", preferred_channel: "telegram", preferred_language: "ru", student_id: "", relation: "" });
      toast.show("Родитель добавлен.", "success");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const makeMagic = async (p: Parent) => {
    setPending(p.id);
    try {
      const { url } = await parentsApi.magicLink(p.id);
      setMagic({ parent: p, url });
    } catch (err) {
      toast.show(errorMessage(err), "error");
    } finally {
      setPending(null);
    }
  };

  const sendInvite = async (p: Parent) => {
    setPending(p.id);
    try {
      const n = await parentsApi.sendInvite(p.id);
      setInvite({ parent: p, notification: n });
      await refresh();
    } catch (err) {
      toast.show(errorMessage(err), "error");
    } finally {
      setPending(null);
    }
  };

  const revokeAccess = async (p: Parent) => {
    const ok = window.confirm(
      `Отозвать доступ родителя «${p.full_name}»? Все ссылки в кабинет и вход на телефоне перестанут работать, ` +
        "Telegram будет отвязан, код приглашения заменён. Затем отправьте новое приглашение.",
    );
    if (!ok) return;
    setPending(p.id);
    try {
      await parentsApi.revokeAccess(p.id);
      await refresh();
      toast.show("Доступ отозван. Отправьте родителю новое приглашение.", "success");
    } catch (err) {
      toast.show(errorMessage(err), "error");
    } finally {
      setPending(null);
    }
  };

  const doLink = async (e: FormEvent) => {
    e.preventDefault();
    if (!linkFor || !linkStudent.student_id) return;
    setBusy(true);
    setError(null);
    try {
      await parentsApi.linkStudent(linkFor.id, { student_id: linkStudent.student_id, relation: linkStudent.relation.trim() || null });
      await refresh();
      setLinkFor(null);
      setLinkStudent({ student_id: "", relation: "" });
      toast.show("Ученик привязан.", "success");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const payloadString = (payload: Record<string, unknown>, key: string): string | null => {
    const v = payload[key];
    return typeof v === "string" && v ? v : null;
  };

  return (
    <>
      <PageHeader
        title="Родители"
        subtitle="Родители получают отчёты в Telegram и входят в кабинет по magic-ссылке."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setCreateOpen(true)}>
            Добавить родителя
          </button>
        }
      />
      <div className="card">
        {q.isLoading && <Skeleton lines={5} />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.items.length === 0 && <EmptyState title="Родителей пока нет" text="Добавьте родителя и привяжите к нему ученика, чтобы отправлять отчёты." />}
        {q.data && q.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Имя</th>
                  <th>Дети</th>
                  <th>Канал</th>
                  <th>Telegram</th>
                  <th>Код приглашения</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <div>{p.full_name}</div>
                      <div className="xs muted">
                        {[p.phone, p.email].filter(Boolean).join(" · ") || "—"} · {LANGUAGE_LABELS[p.preferred_language] ?? p.preferred_language}
                      </div>
                    </td>
                    <td>
                      {p.students.length === 0 ? (
                        <span className="faint small">не привязаны</span>
                      ) : (
                        <div className="stack" style={{ gap: 2 }}>
                          {p.students.map((s) => (
                            <span key={s.id} className="small">
                              <Link to={`/students/${s.id}`}>{s.full_name}</Link>
                              {s.relation ? <span className="muted"> ({s.relation})</span> : null}
                            </span>
                          ))}
                        </div>
                      )}
                      <button type="button" className="link-button xs mt-2" onClick={() => setLinkFor(p)}>
                        + привязать ученика
                      </button>
                    </td>
                    <td>{CHANNEL_LABELS[p.preferred_channel] ?? p.preferred_channel}</td>
                    <td>
                      <Badge tone={p.telegram_linked ? "success" : "neutral"}>{p.telegram_linked ? "Подключён" : "Не подключён"}</Badge>
                    </td>
                    <td className="mono small">{p.invite_code}</td>
                    <td className="nowrap">
                      <div className="row">
                        <button type="button" className="btn btn-sm" disabled={pending === p.id} onClick={() => makeMagic(p)}>
                          Создать magic-ссылку
                        </button>
                        <button type="button" className="btn btn-sm" disabled={pending === p.id} onClick={() => sendInvite(p)}>
                          Отправить приглашение
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          disabled={pending === p.id}
                          title="Потерян телефон, ссылка переслана или привязан чужой Telegram: отключить всё и выдать новый код"
                          onClick={() => revokeAccess(p)}
                        >
                          Отозвать доступ
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {createOpen && (
        <Modal title="Новый родитель" onClose={() => setCreateOpen(false)}>
          <form className="form" onSubmit={submit}>
            <div className="field">
              <label htmlFor="p-name">Имя и фамилия</label>
              <input id="p-name" className="input" required minLength={2} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div className="form-row">
              <div className="field">
                <label htmlFor="p-phone">Телефон</label>
                <input id="p-phone" className="input" placeholder="+7 7xx xxx xx xx" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="p-email">Электронная почта</label>
                <input id="p-email" className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="p-channel">Канал уведомлений</label>
                <select id="p-channel" className="select" value={form.preferred_channel} onChange={(e) => setForm({ ...form, preferred_channel: e.target.value as Channel })}>
                  <option value="telegram">Telegram</option>
                  <option value="whatsapp">WhatsApp</option>
                  <option value="none">Не отправлять</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="p-lang">Язык отчётов</label>
                <select id="p-lang" className="select" value={form.preferred_language} onChange={(e) => setForm({ ...form, preferred_language: e.target.value as Language })}>
                  <option value="ru">Русский</option>
                  <option value="kk">Казахский</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="p-student">Ребёнок</label>
                <select id="p-student" className="select" value={form.student_id} onChange={(e) => setForm({ ...form, student_id: e.target.value })}>
                  <option value="">— привязать позже —</option>
                  {students.data?.items.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.full_name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="p-relation">Кем приходится</label>
                <input id="p-relation" className="input" placeholder="мама / папа / опекун" value={form.relation} onChange={(e) => setForm({ ...form, relation: e.target.value })} />
              </div>
            </div>
            {error && <div className="form-error">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setCreateOpen(false)}>
                Отмена
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Сохраняем…" : "Сохранить"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {linkFor && (
        <Modal title={`Привязать ученика: ${linkFor.full_name}`} onClose={() => setLinkFor(null)}>
          <form className="form" onSubmit={doLink}>
            <div className="field">
              <label htmlFor="l-student">Ученик</label>
              <select id="l-student" className="select" required value={linkStudent.student_id} onChange={(e) => setLinkStudent({ ...linkStudent, student_id: e.target.value })}>
                <option value="">— выберите —</option>
                {students.data?.items
                  .filter((s) => !linkFor.students.some((ls) => ls.id === s.id))
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.full_name}
                    </option>
                  ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="l-relation">Кем приходится</label>
              <input id="l-relation" className="input" placeholder="мама / папа / опекун" value={linkStudent.relation} onChange={(e) => setLinkStudent({ ...linkStudent, relation: e.target.value })} />
            </div>
            {error && <div className="form-error">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setLinkFor(null)}>
                Отмена
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy || !linkStudent.student_id}>
                Привязать
              </button>
            </div>
          </form>
        </Modal>
      )}

      {magic && (
        <Modal title="Magic-ссылка в кабинет родителя" onClose={() => setMagic(null)}>
          <p className="small muted">
            Для {magic.parent.full_name}. Передайте ссылку родителю любым удобным способом — по ней он войдёт в кабинет без пароля.
          </p>
          <CopyBox value={magic.url} />
          <div className="modal-actions">
            <button type="button" className="btn btn-primary" onClick={() => setMagic(null)}>
              Готово
            </button>
          </div>
        </Modal>
      )}

      {invite && (
        <Modal title="Приглашение" onClose={() => setInvite(null)}>
          <div className="row" style={{ marginBottom: 12 }}>
            <span>
              {invite.parent.full_name} · {CHANNEL_LABELS[invite.notification.channel] ?? invite.notification.channel}
            </span>
            <NotificationBadge status={invite.notification.status} />
          </div>
          {invite.notification.status === "sent" && <p className="form-success">Приглашение отправлено {formatDateTime(invite.notification.sent_at)}.</p>}
          {invite.notification.status === "failed" && <p className="form-error">Не удалось отправить: {invite.notification.error ?? "причина не указана"}.</p>}
          {invite.notification.status === "skipped" && (
            <p className="notice notice-warning">
              Автоматическая отправка невозможна (родитель ещё не подключил Telegram-бота или канал не выбран). Перешлите ссылки вручную:
            </p>
          )}
          {(payloadString(invite.notification.payload, "deep_link") ?? payloadString(invite.notification.payload, "telegram_deep_link")) && (
            <div className="field mt-2">
              <span className="field-label">Ссылка на Telegram-бота</span>
              <CopyBox value={payloadString(invite.notification.payload, "deep_link") ?? payloadString(invite.notification.payload, "telegram_deep_link") ?? ""} />
            </div>
          )}
          {(payloadString(invite.notification.payload, "magic_url") ?? payloadString(invite.notification.payload, "url")) && (
            <div className="field mt-2">
              <span className="field-label">Magic-ссылка в кабинет</span>
              <CopyBox value={payloadString(invite.notification.payload, "magic_url") ?? payloadString(invite.notification.payload, "url") ?? ""} />
            </div>
          )}
          <p className="xs faint mt-4">Код приглашения для команды /start в боте: <span className="mono">{invite.parent.invite_code}</span></p>
          <div className="modal-actions">
            <button type="button" className="btn btn-primary" onClick={() => setInvite(null)}>
              Готово
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
