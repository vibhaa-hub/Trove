import { forwardRef, type ButtonHTMLAttributes } from "react";

export type Variant = "primary" | "secondary" | "danger" | "ghost";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  busy?: boolean;
  /** Text announced/shown while busy; defaults to the normal label. */
  busyLabel?: string;
  block?: boolean;
  size?: "md" | "sm";
}

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "secondary", busy = false, busyLabel, block, size = "md", className = "", children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`btn btn-${variant} ${size === "sm" ? "btn-sm" : ""} ${block ? "btn-block" : ""} ${className}`}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {busy && <span className="spinner" aria-hidden="true" />}
      <span>{busy && busyLabel ? busyLabel : children}</span>
    </button>
  );
});
