import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "../shared/api";
import { ErrorNote, Skeleton } from "../shared/components/Display";
import { AppHeader, Footer } from "../shared/components/Layout";
import type { AuthStatus } from "../shared/types";
import { api, UNAUTHORIZED_EVENT } from "./api";
import { AuthScreen } from "./AuthScreen";
import { Workspace } from "./Workspace";

export function OwnerApp() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.get<AuthStatus>("/auth/status"));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onUnauthorized = () => {
      setExpired(true);
      setStatus((current) => (current ? { ...current, authenticated: false, user: null } : current));
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  if (status?.authenticated) {
    return (
      <Workspace
        user={status.user ?? null}
        onSignedOut={() => {
          setExpired(false);
          void load();
        }}
      />
    );
  }

  return (
    <>
      <AppHeader area="Owner workspace" home="/" />
      <main id="main" className="auth-wrap" tabIndex={-1}>
        {error ? (
          <div className="auth-card">
            <ErrorNote message={error} onRetry={load} />
          </div>
        ) : !status ? (
          <div className="auth-card card">
            <Skeleton lines={4} label="Checking your session" />
          </div>
        ) : (
          <AuthScreen
            status={status}
            expired={expired}
            onAuthenticated={() => {
              setExpired(false);
              void load();
            }}
          />
        )}
      </main>
      <Footer />
    </>
  );
}
