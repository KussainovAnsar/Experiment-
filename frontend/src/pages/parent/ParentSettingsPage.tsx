/** /parent/settings — preferred channel and language (PATCH /api/parent/me). */
import { useEffect, useState, type FormEvent } from "react";
import { errorMessage } from "@/api/client";
import { parentApi } from "@/api/parent";
import { useParentMe } from "@/api/queries";
import { useParentAuth } from "@/auth/ParentAuth";
import { BackLink, ErrorState, Skeleton } from "@/components/ui";
import type { Channel, Language } from "@/types/api";

export function ParentSettingsPage() {
  const { updateParent, logout } = useParentAuth();
  const q = useParentMe();
  const [channel, setChannel] = useState<Channel>("telegram");
  const [language, setLanguage] = useState<Language>("ru");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (q.data) {
      setChannel(q.data.preferred_channel);
      setLanguage(q.data.preferred_language);
    }
  }, [q.data]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const me = await parentApi.updateMe({ preferred_channel: channel, preferred_language: language });
      updateParent(me);
      setMsg({ ok: true, text: "Настройки сохранены." });
    } catch (err) {
      setMsg({ ok: false, text: errorMessage(err) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <BackLink to="/parent">На главную</BackLink>
      <h1 style={{ fontSize: "var(--text-xl)", marginTop: 8 }}>Настройки</h1>
      {q.isLoading && <Skeleton lines={4} />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <form className="section form" onSubmit={submit}>
          <div className="kv">
            <dt>Имя</dt>
            <dd>{q.data.full_name}</dd>
            {q.data.center_name && (
              <>
                <dt>Центр</dt>
                <dd>{q.data.center_name}</dd>
              </>
            )}
            <dt>Telegram</dt>
            <dd>{q.data.telegram_linked ? "подключён" : "не подключён — отправьте боту /start с кодом от центра"}</dd>
          </div>
          <div className="field">
            <label htmlFor="ch">Куда присылать отчёты</label>
            <select id="ch" className="select" value={channel} onChange={(e) => setChannel(e.target.value as Channel)}>
              <option value="telegram">Telegram</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="none">Не присылать (смотреть только в кабинете)</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="lang">Язык отчётов</label>
            <select id="lang" className="select" value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
              <option value="ru">Русский</option>
              <option value="kk">Қазақша</option>
            </select>
          </div>
          {msg && <div className={msg.ok ? "form-success" : "form-error"}>{msg.text}</div>}
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Сохраняем…" : "Сохранить"}
          </button>
          <button type="button" className="link-button small" onClick={logout}>
            Выйти из кабинета
          </button>
        </form>
      )}
    </>
  );
}
