import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="friendly-page">
      <div className="inner">
        <div className="emoji" aria-hidden="true">
          🧭
        </div>
        <h2>Страница не найдена</h2>
        <p className="muted">Возможно, ссылка устарела или была введена с ошибкой.</p>
        <Link to="/" className="btn">
          На главную
        </Link>
      </div>
    </div>
  );
}
