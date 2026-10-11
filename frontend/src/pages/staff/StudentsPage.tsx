import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { errorMessage } from "@/api/client";
import { keys, useStudents } from "@/api/queries";
import { studentsApi } from "@/api/staff";
import { useStaffAuth } from "@/auth/StaffAuth";
import { EmptyState, ErrorState, Modal, PageHeader, Skeleton } from "@/components/ui";
import { LANGUAGE_LABELS } from "@/lib/status";
import type { Language } from "@/types/api";

export function StudentsPage() {
  const { canManagePeople } = useStaffAuth();
  const q = useStudents();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ full_name: "", grade: "", school: "", native_language: "kk" as Language });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await studentsApi.create({
        full_name: form.full_name.trim(),
        grade: form.grade ? Number(form.grade) : null,
        school: form.school.trim() || null,
        native_language: form.native_language,
      });
      await qc.invalidateQueries({ queryKey: keys.students });
      setOpen(false);
      setForm({ full_name: "", grade: "", school: "", native_language: "kk" });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Ученики"
        subtitle="Прогресс по навыкам накапливается от урока к уроку."
        actions={
          canManagePeople && (
            <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
              Добавить ученика
            </button>
          )
        }
      />
      <div className="card">
        {q.isLoading && <Skeleton lines={6} />}
        {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
        {q.data && q.data.items.length === 0 && (
          <EmptyState title="Учеников пока нет" text="Добавьте ученика, чтобы загружать записи его уроков." />
        )}
        {q.data && q.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Имя</th>
                  <th>Класс</th>
                  <th>Школа</th>
                  <th>Родной язык</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/students/${s.id}`}>{s.full_name}</Link>
                    </td>
                    <td>{s.grade ?? "—"}</td>
                    <td>{s.school ?? "—"}</td>
                    <td>{LANGUAGE_LABELS[s.native_language] ?? s.native_language}</td>
                    <td className="nowrap">
                      <Link to={`/students/${s.id}`} className="btn btn-sm">
                        Прогресс
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {open && (
        <Modal title="Новый ученик" onClose={() => setOpen(false)}>
          <form className="form" onSubmit={submit}>
            <div className="field">
              <label htmlFor="s-name">Имя и фамилия</label>
              <input id="s-name" className="input" required minLength={2} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
            </div>
            <div className="form-row">
              <div className="field">
                <label htmlFor="s-grade">Класс</label>
                <input id="s-grade" className="input" type="number" min={0} max={12} value={form.grade} onChange={(e) => setForm({ ...form, grade: e.target.value })} />
              </div>
              <div className="field">
                <label htmlFor="s-lang">Родной язык</label>
                <select id="s-lang" className="select" value={form.native_language} onChange={(e) => setForm({ ...form, native_language: e.target.value as Language })}>
                  <option value="kk">Казахский</option>
                  <option value="ru">Русский</option>
                </select>
              </div>
            </div>
            <div className="field">
              <label htmlFor="s-school">Школа</label>
              <input id="s-school" className="input" value={form.school} onChange={(e) => setForm({ ...form, school: e.target.value })} />
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
