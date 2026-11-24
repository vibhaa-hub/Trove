/** Small presentational components: cards, badges, steppers, gates, empty and loading states. */
import { useId, useState, type ReactNode } from "react";
import { humanize, percent } from "../format";
import type { Gate, RiskFlag, Step } from "../types";

export function Card({
  title,
  level = 2,
  actions,
  description,
  children,
  className = "",
  busy,
}: {
  title: ReactNode;
  level?: 2 | 3;
  actions?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
  busy?: boolean;
}) {
  const id = useId();
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section className={`card ${className}`} aria-labelledby={id} aria-busy={busy || undefined}>
      <header className="card-header">
        <div>
          <Heading id={id} className="card-title">
            {title}
          </Heading>
          {description && <p className="muted card-desc">{description}</p>}
        </div>
        {actions && <div className="card-actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export type Tone = "success" | "warning" | "danger" | "info" | "neutral";

const TONE_ICON: Record<Tone, string> = { success: "✓", warning: "!", danger: "✕", info: "i", neutral: "•" };

/** Never colour alone: every badge has an icon and text (WCAG 1.4.1). */
export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      <span aria-hidden="true" className="badge-icon">
        {TONE_ICON[tone]}
      </span>
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  confirmed: "success",
  verified: "success",
  reviewed: "success",
  released: "success",
  settled: "success",
  approved: "success",
  active: "success",
  found: "success",
  promoted: "success",
  documents_reviewed: "success",
  needs_review: "warning",
  pending_review: "warning",
  pending: "warning",
  open: "info",
  invited: "info",
  enrolled: "info",
  new: "info",
  submitted: "info",
  under_review: "info",
  documents_requested: "info",
  frozen: "danger",
  rejected: "danger",
  revoked: "danger",
  cancelled: "danger",
  locked: "danger",
  suspended: "danger",
  gap: "warning",
  high: "danger",
  medium: "warning",
  low: "info",
  info: "neutral",
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const value = status ?? "unknown";
  return <Badge tone={STATUS_TONES[value] ?? "neutral"}>{humanize(value)}</Badge>;
}

/** Ordered progress steps; the first unfinished one is marked aria-current="step". */
export function Stepper({ steps, label, compact }: { steps: Step[]; label: string; compact?: boolean }) {
  const current = steps.findIndex((s) => !s.done);
  return (
    <ol className={`stepper ${compact ? "compact" : ""}`} aria-label={label}>
      {steps.map((step, i) => {
        const state = step.done ? "done" : i === current ? "current" : "todo";
        return (
          <li key={step.key} className={`step step-${state}`} aria-current={i === current ? "step" : undefined}>
            <span className="step-marker" aria-hidden="true">
              {step.done ? "✓" : i + 1}
            </span>
            <span className="step-label">
              {step.label}
              {step.state && !step.done && step.state !== "not_submitted" && (
                <span className="step-state"> · {humanize(step.state)}</span>
              )}
              <span className="sr-only">{step.done ? " (complete)" : i === current ? " (current step)" : " (not started)"}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function GateList({ gates }: { gates: Gate[] }) {
  const passed = gates.filter((g) => g.passed).length;
  return (
    <div>
      <p className="gate-summary">
        <strong>
          {passed} of {gates.length}
        </strong>{" "}
        release gates passed
      </p>
      <progress className="gate-progress" max={gates.length} value={passed} aria-label="Release gates passed">
        {passed} of {gates.length}
      </progress>
      <ul className="gate-list">
        {gates.map((gate) => (
          <li key={gate.label} className={gate.passed ? "pass" : "fail"}>
            <span aria-hidden="true">{gate.passed ? "✓" : "✕"}</span> {gate.label}
            <span className="sr-only">{gate.passed ? ": passed" : ": not yet passed"}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RiskFlags({ flags }: { flags: RiskFlag[] }) {
  if (!flags.length) return <p className="muted">No fraud signals.</p>;
  return (
    <ul className="flag-list">
      {flags.map((flag, i) => (
        <li key={i}>
          <StatusBadge status={flag.level} /> {flag.message}
        </li>
      ))}
    </ul>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="muted">{children}</div>}
      {action}
    </div>
  );
}

export function Skeleton({ lines = 3, label = "Loading" }: { lines?: number; label?: string }) {
  return (
    <div className="skeleton" role="status" aria-label={label}>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skeleton-line" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="form-error" role="alert">
      <span aria-hidden="true">⚠ </span>
      {message}
      {onRetry && (
        <button type="button" className="link-button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Stat({ label, value, tone = "neutral", hint }: { label: string; value: ReactNode; tone?: Tone; hint?: string }) {
  return (
    <div className={`stat stat-${tone}`}>
      <dt>{label}</dt>
      <dd>
        <span className="stat-value">{value}</span>
        {hint && <span className="stat-hint">{hint}</span>}
      </dd>
    </div>
  );
}

export function ConfidenceMeter({ value }: { value: number }) {
  const tone = value >= 0.6 ? "high" : value >= 0.45 ? "mid" : "low";
  const word = tone === "high" ? "high" : tone === "mid" ? "medium" : "low";
  return (
    <span className={`confidence confidence-${tone}`}>
      <span className="confidence-bar" aria-hidden="true">
        <span style={{ width: percent(value) }} />
      </span>
      <span>
        {percent(value)}
        <span className="sr-only"> ({word} confidence)</span>
      </span>
    </span>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-secondary btn-sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 2500);
        } catch {
          setCopied(false);
        }
      }}
    >
      <span aria-live="polite">{copied ? "Copied" : label}</span>
    </button>
  );
}

export function Disclosure({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <details className="disclosure">
      <summary>{summary}</summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
