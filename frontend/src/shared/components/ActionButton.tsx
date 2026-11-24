import { useEffect, useRef, useState, type ComponentProps } from "react";
import { errorMessage } from "../api";
import { Button } from "./Button";
import { useToast } from "./Toaster";

type Props = Omit<ComponentProps<typeof Button>, "onClick" | "busy"> & {
  /** Resolve with a message to show it as a success toast. Errors become error toasts. */
  onAction: () => Promise<string | void>;
};

/** A button that runs an async action, shows its own busy state and reports the result. */
export function ActionButton({ onAction, ...props }: Props) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return (
    <Button
      {...props}
      busy={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const message = await onAction();
          if (message) toast.success(message);
        } catch (error) {
          toast.error(errorMessage(error));
        } finally {
          if (mounted.current) setBusy(false);
        }
      }}
    />
  );
}
