import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { errorMessage } from "@/api/client";
import { authApi } from "@/api/staff";
import { useStaffAuth } from "@/auth/StaffAuth";
import { Brand } from "@/components/StaffLayout";
import { Notice } from "@/components/ui";

export function LoginPage() {
  const { applyToken } = useStaffAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as { from?: string; expired?: boolean };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const resp = await authApi.login({ email: email.trim(), password });
      applyToken(resp);
      navigate(state.from && state.from !== "/login" ? state.from : "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err, "Не удалось войти. Проверьте почту и пароль."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <Brand />
        <h1 style={{ fontSize: "var(--text-xl)", textAlign: "center" }}>Вход для сотрудников центра</h1>
        {state.expired && <Notice tone="warning">Сессия истекла. Войдите снова.</Notice>}
        <form className="form mt-4" onSubmit={submit}>
          <div className="field">
            <label htmlFor="email">Электронная почта</label>
            <input
              id="email"
              className="input"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">Пароль</label>
            <input
              id="password"
              className="input"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
            {busy ? "Входим…" : "Войти"}
          </button>
        </form>
        <p className="auth-footer">
          Ещё нет аккаунта центра? <Link to="/register">Зарегистрировать центр</Link>
        </p>
      </div>
    </div>
  );
}
