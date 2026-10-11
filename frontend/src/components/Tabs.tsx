/** Accessible tab strip. */
export interface TabItem<K extends string> {
  key: K;
  label: string;
  count?: number;
  disabled?: boolean;
}

export function Tabs<K extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={t.key === active}
          className={`tab${t.key === active ? " active" : ""}`}
          disabled={t.disabled}
          onClick={() => onChange(t.key)}
        >
          {t.label}
          {t.count !== undefined && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
