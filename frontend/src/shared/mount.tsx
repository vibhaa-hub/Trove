import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { ConfirmProvider } from "./components/Dialog";
import { ToastProvider } from "./components/Toaster";
import "./styles.css";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <ConfirmProvider>{children}</ConfirmProvider>
    </ToastProvider>
  );
}

export function mount(app: ReactNode): void {
  const root = document.getElementById("root");
  if (!root) throw new Error("#root element missing");
  createRoot(root).render(
    <StrictMode>
      <Providers>{app}</Providers>
    </StrictMode>,
  );
}
