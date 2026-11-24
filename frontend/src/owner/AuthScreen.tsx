import { useState, type FormEvent } from "react";
import { errorMessage } from "../shared/api";
import { Button } from "../shared/components/Button";
import { FormError, PasswordField, TextField } from "../shared/components/Field";
import { useDocumentTitle } from "../shared/hooks";
import type { AuthStatus } from "../shared/types";
import { api } from "./api";

type Mode = "setup" | "login" | "register";

const COPY: Record<Mode, { title: string; lede: string; submit: string }> = {
  setup: {
    title: "Secure your workspace",
    lede: "Create the owner password that protects this Trove workspace.",
    submit: "Create password",
  },
  login: { title: "Sign in to Trove", lede: "Welcome back. Sign in to your workspace.", submit: "Sign in" },
  register: { title: "Create an account", lede: "Each account sees only its own cases.", submit: "Create account" },
};

export function AuthScreen({
  status,
  expired,
  onAuthenticated,
}: {
  status: AuthStatus;
  expired: boolean;
  onAuthenticated: () => void;
}) {
  const [mode, setMode] = useState<Mode>(status.setup_required ? "setup" : "login");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const copy = COPY[mode];
  useDocumentTitle(`${copy.title} · Trove`);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    setBusy(true);
    setError(null);
    try {
      await api.post(`/auth/${mode}`, email ? { email, password } : { password });
      onAuthenticated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const isNew = mode !== "login";
  return (
    <section className="card auth-card" aria-labelledby="auth-title">
      <h1 id="auth-title">{copy.title}</h1>
      <p className="muted">{copy.lede}</p>
      {expired && mode === "login" && (
        <div className="callout callout-warning" role="status">
          <p>Your session ended. Sign in again to continue.</p>
        </div>
      )}
      <form className="stack" onSubmit={submit} noValidate={false} key={mode}>
        <FormError message={error} />
        <TextField
          label="Email"
          name="email"
          type="email"
          autoComplete={isNew ? "email" : "username"}
          maxLength={254}
          optional={mode !== "register"}
          required={mode === "register"}
          hint={mode === "register" ? undefined : "Leave empty if this is a single-owner local workspace."}
        />
        <PasswordField label="Password" name="password" required minLength={12} maxLength={1024} isNew={isNew} />
        <Button type="submit" variant="primary" block busy={busy} busyLabel="Please wait…">
          {copy.submit}
        </Button>
      </form>
      {!status.setup_required && status.signup_allowed && (
        <p className="auth-alt">
          {mode === "login" ? (
            <button type="button" className="link-button" onClick={() => setMode("register")}>
              New here? Create an account
            </button>
          ) : (
            <button type="button" className="link-button" onClick={() => setMode("login")}>
              Already have an account? Sign in
            </button>
          )}
        </p>
      )}
      <p className="hint">
        Passwords are stored as salted hashes; emails are encrypted. Nominees sign in at <a href="/nominee">the nominee portal</a>; reviewers
        at <a href="/reviewer">the reviewer console</a>.
      </p>
    </section>
  );
}
