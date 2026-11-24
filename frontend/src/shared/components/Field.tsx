import {
  useEffect,
  useId,
  useRef,
  useState,
  type DragEvent,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { passwordChecks } from "../format";

interface FieldShell {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  /** Show "(optional)" instead of marking required fields. */
  optional?: boolean;
  className?: string;
}

interface ShellProps extends FieldShell {
  id: string;
  required?: boolean;
  children: ReactNode;
}

function Shell({ id, label, hint, error, optional, required, className = "", children }: ShellProps) {
  return (
    <div className={`field ${error ? "field-invalid" : ""} ${className}`}>
      <label htmlFor={id}>
        {label}
        {required && (
          <span className="req" aria-hidden="true">
            {" "}*
          </span>
        )}
        {optional && <span className="opt"> (optional)</span>}
      </label>
      {hint && (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      )}
      {children}
      {error && (
        <p id={`${id}-error`} className="field-error">
          <span aria-hidden="true">⚠ </span>
          {error}
        </p>
      )}
    </div>
  );
}

function describedBy(id: string, hint: unknown, error: unknown, extra?: string) {
  return [hint ? `${id}-hint` : "", error ? `${id}-error` : "", extra ?? ""].filter(Boolean).join(" ") || undefined;
}

type InputProps = FieldShell & Omit<InputHTMLAttributes<HTMLInputElement>, "id">;

export function TextField({ label, hint, error, optional, className, ...input }: InputProps) {
  const id = useId();
  return (
    <Shell id={id} label={label} hint={hint} error={error} optional={optional} required={input.required} className={className}>
      <input
        id={id}
        aria-describedby={describedBy(id, hint, error)}
        aria-invalid={error ? true : undefined}
        {...input}
      />
    </Shell>
  );
}

type SelectProps = FieldShell & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> & {
  options: { value: string; label: string }[];
};

export function SelectField({ label, hint, error, optional, className, options, ...select }: SelectProps) {
  const id = useId();
  return (
    <Shell id={id} label={label} hint={hint} error={error} optional={optional} required={select.required} className={className}>
      <select id={id} aria-describedby={describedBy(id, hint, error)} aria-invalid={error ? true : undefined} {...select}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Shell>
  );
}

type AreaProps = FieldShell & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id">;

export function TextArea({ label, hint, error, optional, className, ...area }: AreaProps) {
  const id = useId();
  return (
    <Shell id={id} label={label} hint={hint} error={error} optional={optional} required={area.required} className={className}>
      <textarea id={id} aria-describedby={describedBy(id, hint, error)} aria-invalid={error ? true : undefined} {...area} />
    </Shell>
  );
}

/** Password with show/hide toggle and a live requirements checklist (for new passwords). */
export function PasswordField({
  label,
  hint,
  error,
  className,
  isNew = false,
  ...input
}: InputProps & { isNew?: boolean }) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  const [value, setValue] = useState("");
  const checks = passwordChecks(value);
  const reqId = `${id}-req`;
  return (
    <Shell id={id} label={label} hint={hint} error={error} required={input.required} className={className}>
      <div className="input-with-action">
        <input
          id={id}
          type={visible ? "text" : "password"}
          autoComplete={isNew ? "new-password" : "current-password"}
          spellCheck={false}
          aria-describedby={describedBy(id, hint, error, isNew ? reqId : undefined)}
          aria-invalid={error ? true : undefined}
          onChange={(event) => setValue(event.target.value)}
          {...input}
        />
        <button
          type="button"
          className="input-action"
          aria-pressed={visible}
          aria-controls={id}
          onClick={() => setVisible((v) => !v)}
        >
          {visible ? "Hide" : "Show"}
          <span className="sr-only"> password</span>
        </button>
      </div>
      {isNew && (
        <ul id={reqId} className="requirements">
          <li className={checks.length ? "met" : ""}>
            <span aria-hidden="true">{checks.length ? "✓" : "○"} </span>
            At least 12 characters
            <span className="sr-only">{checks.length ? " (met)" : " (not met yet)"}</span>
          </li>
          <li className={checks.classes ? "met" : ""}>
            <span aria-hidden="true">{checks.classes ? "✓" : "○"} </span>
            3 of: lowercase, uppercase, number, symbol
            <span className="sr-only">{checks.classes ? " (met)" : " (not met yet)"}</span>
          </li>
        </ul>
      )}
    </Shell>
  );
}

/** One-time code: numeric keyboard, paste-friendly, autofill from SMS (WCAG 3.3.8). */
export function CodeField(props: InputProps) {
  return (
    <TextField
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9]{6}"
      maxLength={6}
      minLength={6}
      spellCheck={false}
      className="code-field"
      title="6-digit code"
      {...props}
    />
  );
}

type FileProps = FieldShell & {
  name: string;
  accept?: string;
  required?: boolean;
  onFile?: (file: File | null) => void;
};

/** Native file input (keyboard + screen-reader friendly) with an optional drag-and-drop zone. */
export function FileField({ label, hint, error, optional, className, name, accept, required, onFile }: FileProps) {
  const id = useId();
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // clear the "Selected: …" note when the surrounding form is reset after a submit
  useEffect(() => {
    const form = inputRef.current?.form;
    if (!form) return;
    const onReset = () => {
      setFile(null);
      onFile?.(null);
    };
    form.addEventListener("reset", onReset);
    return () => form.removeEventListener("reset", onReset);
  }, [onFile]);

  function choose(next: File | null, input?: HTMLInputElement | null) {
    setFile(next);
    onFile?.(next);
    if (input && next) {
      const transfer = new DataTransfer();
      transfer.items.add(next);
      input.files = transfer.files;
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) choose(dropped, event.currentTarget.querySelector("input"));
  }

  return (
    <Shell id={id} label={label} hint={hint} error={error} optional={optional} required={required} className={className}>
      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          id={id}
          type="file"
          name={name}
          accept={accept}
          required={required}
          aria-describedby={describedBy(id, hint, error, `${id}-chosen`)}
          aria-invalid={error ? true : undefined}
          onChange={(event) => choose(event.target.files?.[0] ?? null)}
        />
        <p id={`${id}-chosen`} className="dropzone-note">
          {file ? `Selected: ${file.name} (${Math.max(1, Math.round(file.size / 1024))} KB)` : "or drag a file here"}
        </p>
      </div>
    </Shell>
  );
}

interface CheckOption {
  value: string;
  label: string;
}

export function CheckboxGroup({
  legend,
  name,
  options,
  defaultChecked = [],
  hint,
  columns = 1,
}: {
  legend: ReactNode;
  name: string;
  options: CheckOption[];
  defaultChecked?: string[];
  hint?: ReactNode;
  columns?: 1 | 2;
}) {
  const id = useId();
  return (
    <fieldset className="checks" aria-describedby={hint ? `${id}-hint` : undefined}>
      <legend>{legend}</legend>
      {hint && (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      )}
      <div className={columns === 2 ? "checks-grid" : "checks-list"}>
        {options.map((option) => (
          <label key={option.value} className="check">
            <input type="checkbox" name={name} value={option.value} defaultChecked={defaultChecked.includes(option.value)} />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function Checkbox({
  label,
  hint,
  ...input
}: { label: ReactNode; hint?: ReactNode } & Omit<InputHTMLAttributes<HTMLInputElement>, "type">) {
  const id = useId();
  return (
    <div className="check-single">
      <input id={id} type="checkbox" aria-describedby={hint ? `${id}-hint` : undefined} {...input} />
      <label htmlFor={id}>{label}</label>
      {hint && (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      )}
    </div>
  );
}

/** Form-level error summary; announced immediately (role=alert). */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="form-error" role="alert">
      <span aria-hidden="true">⚠ </span>
      {message}
    </div>
  );
}
