/** Telegram message preview: renders sanitised aiogram-compatible HTML in a chat bubble. */
import { sanitizeTelegramHtml, stripTags } from "@/lib/sanitize";

export function TelegramPreview({ html }: { html: string | null | undefined }) {
  if (!html) {
    return <p className="muted small">Текст сообщения для Telegram ещё не сформирован.</p>;
  }
  const safe = sanitizeTelegramHtml(html);
  const length = stripTags(html).length;
  return (
    <div className="tg-preview">
      <div className="tg-bubble" dangerouslySetInnerHTML={{ __html: safe }} />
      <div className="tg-meta">
        {length} символов{length > 1500 ? " · превышает рекомендуемые 1500" : ""}
      </div>
    </div>
  );
}
