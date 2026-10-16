import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorMessage } from "@/api/client";
import { keys, subjectTitle, useSubjects, useTeachers } from "@/api/queries";
import { teachersApi } from "@/api/staff";
import { useToast } from "@/components/Toast";
import { Badge, EmptyState, ErrorState, Modal, PageHeader, Skeleton } from "@/components/ui";

export function TeachersPage() {
  const q = useTeachers();
  const subjects = useSubjects();
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", subjects: [] as string[] });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (subjects.data?.length && form.subjects.length === 0) {
      setError("Отметьте хотя бы один предмет.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await teachersApi.create({
        full_name: form.full_name.trim(),
        subjects: form.subjects.length ? form.subjects : undefined, // list unavailable → backend default
        email: form.email.trim() || null,
        phone: form.phone.trim() || null,
      });
      await qc.invalidateQueries({ queryKey: keys.teachers });
      setOpen(false);
      setForm({ full_name: "", email: "", phone: "", subjects: [] });
      toast.show("Преподаватель добавлен.", "success");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Удалить преподавателя «${name}»? Его уроки останутся в системе.`)) return;
    try {
      await teachersApi.remove(id);
      await qc.invalidateQueries({ queryKey: keys.teachers });
      toast.show("Преподаватель удалён.");
    } catch (err) {
      toast.show(errorMessage(err), "error");
    }
  };

  return (
    <>
      <PageHeader
        title="Преподаватели"
        subtitle="Справочник преподавателей центра."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
            Добавить преподавателя
          </button>
        }
      />
      <div className="card">
        {q.isLoading && <Skeleton lines={5} />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.items.length === 0 && <EmptyState title="Преподавателей пока нет" text="Добавьте преподавателя, чтобы привязывать к нему уроки." />}
        {q.data && q.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Имя</th>
                  <th>Предметы</th>
                  <th>Контакты</th>
                  <th>Статус</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((t) => (
                  <tr key={t.id}>
                    <td>{t.full_name}</td>
                    <td className="muted">{t.subjects.map((s) => subjectTitle(subjects.data, s)).join(", ") || "—"}</td>
                    <td className="muted small">
                      {t.email ?? ""}
                      {t.email && t.phone ? " · " : ""}
                      {t.phone ?? ""}
                      {!t.email && !t.phone ? "—" : ""}
                    </td>
                    <td>
                      <Badge tone={t.is_active ? "success" : "neutral"}>{t.is_active ? "Активен" : "Неактивен"}</Badge>
                    </td>
                    <td className="nowrap">
                      <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(t.id, t.full_name)}>
                        Удалить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {open && (
        <Modal title="Новый преподаватель" onClose={() => setOpen(false)}>
          <form className="form" onSubmit={submit}>
            <div className="field">
              <label htmlFor="t-name">Имя и фамилия</label>
              <input id="t-name" className="input" required minLength={2} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div className="field">
              <span className="field-label">Предметы</span>
              <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
                {subjects.data?.map((s) => (
                  <label key={s.code} className="checkbox">
                    <input
                      type="checkbox"
                      checked={form.subjects.includes(s.code)}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          subjects: e.target.checked ? [...form.subjects, s.code] : form.subjects.filter((c) => c !== s.code),
                        })
                      }
                    />
                    {s.title}
                  </label>
                ))}
              </div>
              {subjects.isError && <span className="field-hint">Не удалось загрузить список предметов.</span>}
            </div>
            <div className="form-row">
              <div className="field">
                <label htmlFor="t-email">Электронная почта</label>
                <input id="t-email" className="input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="t-phone">Телефон</label>
                <input id="t-phone" className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              </div>
            </div>
            {error && <div className="form-error">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setOpen(false)}>
                Отмена
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Сохраняем…" : "Сохранить"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
