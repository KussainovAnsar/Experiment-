import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { errorMessage } from "@/api/client";
import { authApi } from "@/api/staff";
import { useStaffAuth } from "@/auth/StaffAuth";
import { Brand } from "@/components/StaffLayout";

export function RegisterPage() {
  const { applyToken } = useStaffAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ center_name: "", city: "", owner_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const update = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (form.password.length < 8) {
      setError("Пароль должен содержать не менее 8 символов.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const resp = await authApi.registerCenter({
        center_name: form.center_name.trim(),
        city: form.city.trim() || null,
        owner_name: form.owner_name.trim(),
        email: form.email.trim(),
        password: form.password,
      });
      applyToken(resp);
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err, "Не удалось зарегистрировать центр."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <Brand />
        <h1 style={{ fontSize: "var(--text-xl)", textAlign: "center" }}>Регистрация центра</h1>
        <p className="muted small" style={{ textAlign: "center" }}>
          Создайте аккаунт владельца. Преподавателей, учеников и родителей вы добавите позже.
        </p>
        <form className="form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="center_name">Название центра</label>
            <input id="center_name" className="input" required minLength={2} value={form.center_name} onChange={update("center_name")} />
          </div>
          <div className="field">
            <label htmlFor="city">Город</label>
            <input id="city" className="input" placeholder="Алматы" value={form.city} onChange={update("city")} />
          </div>
          <div className="field">
            <label htmlFor="owner_name">Ваше имя</label>
            <input id="owner_name" className="input" required minLength={2} value={form.owner_name} onChange={update("owner_name")} />
          </div>
          <div className="field">
            <label htmlFor="reg_email">Электронная почта</label>
            <input id="reg_email" className="input" type="email" autoComplete="username" required value={form.email} onChange={update("email")} />
          </div>
          <div className="field">
            <label htmlFor="reg_password">Пароль</label>
            <input
              id="reg_password"
              className="input"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={form.password}
              onChange={update("password")}
            />
            <span className="field-hint">Не менее 8 символов.</span>
          </div>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
            {busy ? "Создаём…" : "Создать центр"}
          </button>
        </form>
        <p className="auth-footer">
          Уже есть аккаунт? <Link to="/login">Войти</Link>
        </p>
      </div>
    </div>
  );
}
