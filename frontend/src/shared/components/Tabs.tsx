import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  badge?: ReactNode;
  panel: ReactNode;
}

/**
 * WAI-ARIA APG tabs: roving tabindex, Left/Right/Home/End move between tabs and activate
 * them (automatic activation). Hidden panels stay unmounted so their forms don't keep stale data.
 */
export function Tabs<K extends string>({
  label,
  items,
  active,
  onChange,
}: {
  label: string;
  items: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
}) {
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const index = Math.max(0, items.findIndex((item) => item.key === active));

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const last = items.length - 1;
    const moves: Record<string, number> = {
      ArrowRight: index === last ? 0 : index + 1,
      ArrowLeft: index === 0 ? last : index - 1,
      Home: 0,
      End: last,
    };
    const target = moves[event.key];
    if (target === undefined) return;
    event.preventDefault();
    onChange(items[target].key);
    refs.current[target]?.focus();
  }

  return (
    <div className="tabs">
      <div role="tablist" aria-label={label} className="tablist" onKeyDown={onKeyDown}>
        {items.map((item, i) => (
          <button
            key={item.key}
            ref={(node) => {
              refs.current[i] = node;
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${item.key}`}
            aria-controls={`${base}-panel-${item.key}`}
            aria-selected={i === index}
            tabIndex={i === index ? 0 : -1}
            className="tab"
            onClick={() => onChange(item.key)}
          >
            {item.label}
            {item.badge !== undefined && item.badge !== null && <span className="tab-badge">{item.badge}</span>}
          </button>
        ))}
      </div>
      {items.map((item, i) => (
        <div
          key={item.key}
          role="tabpanel"
          id={`${base}-panel-${item.key}`}
          aria-labelledby={`${base}-tab-${item.key}`}
          hidden={i !== index}
          tabIndex={0}
          className="tabpanel"
        >
          {i === index && item.panel}
        </div>
      ))}
    </div>
  );
}
