/** /p/:token — exchanges the magic token for a parent JWT and redirects to the portal. */
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { errorMessage } from "@/api/client";
import { parentApi } from "@/api/parent";
import { useParentAuth } from "@/auth/ParentAuth";
import { Brand } from "@/components/StaffLayout";

export function MagicLinkPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const { applyExchange } = useParentAuth();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!token) {
      setError("Ссылка неполная.");
      return;
    }
    parentApi
      .exchange(token)
      .then((resp) => {
        applyExchange(resp);
        navigate("/parent", { replace: true });
      })
      .catch((err) => setError(errorMessage(err, "Не удалось открыть ссылку.")));
  }, [token, applyExchange, navigate]);

  return (
    <div className="friendly-page">
      <div className="inner">
        <Brand sub="кабинет родителя" />
        {error ? (
          <>
            <div className="emoji" aria-hidden="true">
              ⏳
            </div>
            <h2>Ссылка устарела</h2>
            <p className="muted">Запросите новую ссылку у учебного центра — её пришлют в Telegram или сообщением.</p>
            <p className="xs faint">{error}</p>
            <Link to="/parent/expired" className="btn">
              Подробнее
            </Link>
          </>
        ) : (
          <>
            <div className="skeleton mt-4" style={{ height: 18 }} />
            <p className="muted mt-4">Открываем кабинет…</p>
          </>
        )}
      </div>
    </div>
  );
}
