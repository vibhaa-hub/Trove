import { useState, type FormEvent } from "react";
import { errorMessage } from "../api";
import { useToast } from "./Toaster";

/**
 * Standard form submission: busy flag, inline error (rendered with <FormError>), success toast
 * and optional reset. The handler returns an optional success message.
 */
export function useFormSubmit(
  handler: (data: FormData, form: HTMLFormElement) => Promise<string | void>,
  { reset = true }: { reset?: boolean } = {},
) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError(null);
    try {
      const message = await handler(new FormData(form), form);
      if (reset) form.reset();
      if (message) toast.success(message);
    } catch (err) {
      const text = errorMessage(err);
      setError(text);
      // move focus to the form so the error is read in context
      form.querySelector<HTMLElement>("[role=alert]")?.focus?.();
    } finally {
      setBusy(false);
    }
  }

  return { onSubmit, busy, error };
}

export const str = (data: FormData, key: string) => String(data.get(key) ?? "").trim();
