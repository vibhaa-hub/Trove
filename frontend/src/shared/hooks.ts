import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";

export interface Resource<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (value: T) => void;
}

/**
 * Load data whenever `deps` change. Stale responses are dropped. Existing data stays visible
 * while a refresh is in flight (no skeleton flash); remount with a React `key` to start clean.
 * Pass `enabled = false` to skip loading.
 */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: unknown[],
  enabled = true,
): Resource<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(enabled);
  const controller = useRef<AbortController | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    try {
      const value = await loadRef.current(current.signal);
      if (!current.signal.aborted) {
        setData(value);
        setError(null);
      }
    } catch (err) {
      if (!current.signal.aborted && (err as Error).name !== "AbortError") setError(errorMessage(err));
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setData(undefined);
      setLoading(false);
      return;
    }
    void reload();
    return () => controller.current?.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reload, ...deps]);

  return { data, error, loading, reload, setData };
}

/** Run `callback` every `ms` while the tab is visible (and once when it becomes visible again). */
export function usePolling(callback: () => void, ms: number, enabled = true): void {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!enabled) return;
    const tick = () => {
      if (document.visibilityState === "visible") saved.current();
    };
    const id = window.setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [ms, enabled]);
}

/** Minimal hash router: "#/plan" -> "plan". Back/forward and deep links work. */
export function useHashRoute<T extends string>(routes: readonly T[], fallback: T): [T, (route: T) => void] {
  const read = useCallback((): T => {
    const value = window.location.hash.replace(/^#\/?/, "").split("?")[0] as T;
    return routes.includes(value) ? value : fallback;
  }, [routes, fallback]);
  const [route, setRoute] = useState<T>(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, [read]);
  const navigate = useCallback((next: T) => {
    if (window.location.hash !== `#/${next}`) window.location.hash = `/${next}`;
    setRoute(next);
  }, []);
  return [route, navigate];
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title;
  }, [title]);
}

/** Wrap an async action with a busy flag (for disabling buttons and aria-busy). */
export function useAction<A extends unknown[]>(
  action: (...args: A) => Promise<void>,
  onError: (message: string) => void,
): [(...args: A) => Promise<void>, boolean] {
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const run = useCallback(
    async (...args: A) => {
      setBusy(true);
      try {
        await action(...args);
      } catch (error) {
        onError(errorMessage(error));
      } finally {
        if (mounted.current) setBusy(false);
      }
    },
    [action, onError],
  );
  return [run, busy];
}

/** Persisted colour scheme preference: "system" | "light" | "dark". */
export type Theme = "system" | "light" | "dark";
const THEME_KEY = "trove-theme";

function readTheme(): Theme {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      if (theme === "system") window.localStorage.removeItem(THEME_KEY);
      else window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* storage unavailable: preference lasts for this page only */
    }
  }, [theme]);
  return [theme, setTheme];
}
