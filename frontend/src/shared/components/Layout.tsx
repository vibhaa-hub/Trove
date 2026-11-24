import { useEffect, useRef, type ReactNode } from "react";
import { useTheme, type Theme } from "../hooks";

export function SkipLink({ target = "main" }: { target?: string }) {
  return (
    <a className="skip-link" href={`#${target}`}>
      Skip to main content
    </a>
  );
}

export function ThemeSelect() {
  const [theme, setTheme] = useTheme();
  return (
    <label className="theme-select">
      <span className="sr-only">Colour theme</span>
      <span aria-hidden="true" className="theme-icon">
        ◐
      </span>
      <select value={theme} onChange={(event) => setTheme(event.target.value as Theme)}>
        <option value="system">System theme</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

export function AppHeader({ area, home, children }: { area: string; home: string; children?: ReactNode }) {
  return (
    <header className="app-header">
      <SkipLink />
      <a className="brand" href={home}>
        <img src="/favicon.svg" alt="" width={28} height={28} />
        <span className="brand-name">Trove</span>
        <span className="brand-area">{area}</span>
      </a>
      <div className="header-actions">
        {children}
        <ThemeSelect />
      </div>
    </header>
  );
}

/** Page heading that receives focus when `focusKey` changes (route change), per WCAG 2.4.3. */
export function PageHeading({ children, focusKey, lede }: { children: ReactNode; focusKey?: string; lede?: ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    ref.current?.focus();
  }, [focusKey]);
  return (
    <div className="page-heading">
      <h1 ref={ref} tabIndex={-1}>
        {children}
      </h1>
      {lede && <p className="lede">{lede}</p>}
    </div>
  );
}

export function Footer() {
  return (
    <footer className="app-footer">
      <p>
        Trove organises information; it is not legal or financial advice. Release decisions always need human review.
      </p>
      <nav aria-label="Portals">
        <a href="/">Owner workspace</a>
        <a href="/nominee">Nominee portal</a>
        <a href="/reviewer">Reviewer console</a>
      </nav>
    </footer>
  );
}
