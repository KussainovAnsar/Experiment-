/** Minimal toast notifications (context + hook). */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type ToastTone = "default" | "success" | "error";
interface ToastItem {
  id: number;
  text: string;
  tone: ToastTone;
}

interface ToastContextValue {
  show: (text: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue>({ show: () => undefined });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const counter = useRef(0);
  const show = useCallback((text: string, tone: ToastTone = "default") => {
    counter.current += 1;
    const id = counter.current;
    setItems((prev) => [...prev, { id, text, tone }]);
    window.setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), 4000);
  }, []);
  const value = useMemo(() => ({ show }), [show]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast${t.tone !== "default" ? ` toast-${t.tone}` : ""}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}
