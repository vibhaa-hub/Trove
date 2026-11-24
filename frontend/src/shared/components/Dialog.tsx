import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Button } from "./Button";

interface DialogProps {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  size?: "md" | "lg";
}

/**
 * Modal dialog built on native <dialog>.showModal(): the browser provides the focus trap,
 * Escape-to-close and inert background. Focus returns to the opener on close.
 */
export function Dialog({ open, title, description, onClose, children, size = "md" }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      opener.current = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
      opener.current?.focus?.();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={`dialog dialog-${size}`}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      {open && (
        <div className="dialog-body">
          <header className="dialog-header">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="icon-button" onClick={onClose} aria-label="Close dialog">
              ×
            </button>
          </header>
          {description && (
            <p id={descId} className="muted">
              {description}
            </p>
          )}
          {children}
        </div>
      )}
    </dialog>
  );
}

/* ---------------------------------------------------- confirm / prompt */
interface ConfirmOptions {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  /** Ask for text (replaces window.prompt). The resolved value is the text. */
  input?: { label: string; required?: boolean; mustEqual?: string; autoComplete?: string };
}
type Resolver = (value: string | boolean) => void;
type ConfirmFn = (options: ConfirmOptions) => Promise<string | false>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const [value, setValue] = useState("");
  const resolver = useRef<Resolver | null>(null);
  const inputId = useId();

  const confirm = useCallback<ConfirmFn>(
    (next) =>
      new Promise((resolve) => {
        resolver.current = resolve as Resolver;
        setValue("");
        setOptions(next);
      }),
    [],
  );

  function finish(result: string | false) {
    resolver.current?.(result);
    resolver.current = null;
    setOptions(null);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (options?.input?.mustEqual && value !== options.input.mustEqual) return;
    finish(options?.input ? value.trim() : "confirmed");
  }

  const mismatch = Boolean(options?.input?.mustEqual && value !== options.input.mustEqual);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={options !== null} title={options?.title ?? ""} onClose={() => finish(false)}>
        {options && (
          <form onSubmit={submit} className="stack">
            <div className="dialog-message">{options.message}</div>
            {options.input && (
              <div className="field">
                <label htmlFor={inputId}>{options.input.label}</label>
                <input
                  id={inputId}
                  value={value}
                  required={options.input.required ?? true}
                  autoComplete={options.input.autoComplete ?? "off"}
                  autoFocus
                  onChange={(event) => setValue(event.target.value)}
                />
              </div>
            )}
            <div className="button-row end">
              <Button onClick={() => finish(false)} autoFocus={!options.input}>
                Cancel
              </Button>
              <Button type="submit" variant={options.danger ? "danger" : "primary"} disabled={mismatch}>
                {options.confirmLabel ?? "Confirm"}
              </Button>
            </div>
          </form>
        )}
      </Dialog>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const value = useContext(ConfirmContext);
  if (!value) throw new Error("useConfirm must be used inside <ConfirmProvider>");
  return value;
}
