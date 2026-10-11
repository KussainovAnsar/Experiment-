/** Friendly page for an expired or missing parent session. */
import { Brand } from "@/components/StaffLayout";

export function ExpiredPage() {
  return (
    <div className="friendly-page">
      <div className="inner">
        <Brand sub="кабинет родителя" />
        <div className="emoji mt-4" aria-hidden="true">
          🔗
        </div>
        <h2>Ссылка устарела — запросите новую у центра</h2>
        <p className="muted">
          Для входа в кабинет нужна свежая ссылка. Напишите в учебный центр или отправьте боту в Telegram команду
          <span className="mono"> /start</span> — он пришлёт новую ссылку.
        </p>
      </div>
    </div>
  );
}
