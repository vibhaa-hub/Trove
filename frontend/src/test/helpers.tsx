import axe from "axe-core";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { Providers } from "../shared/mount";

type Route = [method: string, pattern: RegExp, status: number, body: unknown];

/** Route-table fetch mock: first matching [method, regex] wins; unmatched calls fail loudly. */
export function mockFetch(routes: Route[]) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const hit = routes.find(([m, re]) => m === method && re.test(url));
    if (!hit) return new Response(JSON.stringify({ error: `unmocked ${method} ${url}` }), { status: 500 });
    const [, , status, body] = hit;
    return new Response(JSON.stringify(typeof body === "function" ? body() : body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  return calls;
}

export function renderApp(ui: ReactNode) {
  return render(<Providers>{ui}</Providers>);
}

/** Run axe against the rendered document with WCAG 2.x A/AA rules. */
export async function axeViolations(container: Element = document.body) {
  const results = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] },
    rules: {
      // jsdom does not compute layout/colours; contrast is checked by design tokens instead
      "color-contrast": { enabled: false },
      region: { enabled: false },
    },
  });
  return results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`);
}
