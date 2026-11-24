import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, type Page } from "@playwright/test";

/* ------------------------------------------------------------------ TOTP */
function base32(secret: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/[\s=]/g, "").toUpperCase()) {
    const value = alphabet.indexOf(char);
    if (value < 0) throw new Error(`bad base32 character ${char}`);
    bits += value.toString(2).padStart(5, "0");
  }
  const bytes = bits.match(/.{8}/g) ?? [];
  return Buffer.from(bytes.map((b) => parseInt(b, 2)));
}

function totpAt(secret: string, step: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(step));
  const digest = createHmac("sha1", base32(secret)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const code = (digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000;
  return String(code).padStart(6, "0");
}

const lastStep = new Map<string, number>();

/**
 * A code the server will accept: it rejects reuse of a time step, and accepts the next step
 * early, so use current, else the next one, else wait for time to move on.
 */
export async function freshTotp(secret: string): Promise<string> {
  for (;;) {
    const now = Math.floor(Date.now() / 30_000);
    const used = lastStep.get(secret) ?? -1;
    const step = used < now ? now : used === now ? now + 1 : -1;
    if (step >= 0) {
      lastStep.set(secret, step);
      return totpAt(secret, step);
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/* ---------------------------------------------------------------- UI aids */
export async function expectToast(page: Page, text: string | RegExp) {
  await expect(page.locator(".toast").filter({ hasText: text }).first()).toBeVisible();
}

/** Read the latest one-time code for a channel from the nominee page's dev inbox card. */
export async function devInboxCode(page: Page, channel: "Email" | "Phone"): Promise<string> {
  let code = "";
  await expect
    .poll(
      async () => {
        const items = await page.locator("li.item").allTextContents();
        const hit = items.find((t) => t.startsWith(`${channel} ·`) && /code: \d{6}/.test(t));
        code = hit?.match(/code: (\d{6})/)?.[1] ?? "";
        return code;
      },
      { timeout: 20_000, message: `waiting for the ${channel} code in the dev inbox` },
    )
    .not.toBe("");
  return code;
}

export function file(name: string, text: string) {
  return { name, mimeType: "text/plain", buffer: Buffer.from(text) };
}

/* ------------------------------------------------------------ accessibility */
const axeSource = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");

/** Run axe (WCAG 2.2 A/AA incl. real colour contrast) in the live page and fail on violations. */
export async function expectAccessible(page: Page, label: string) {
  // toasts float over content for a few seconds; dismiss them so they don't skew target-size checks
  // (best effort: a toast may auto-dismiss between finding and clicking it)
  for (const button of await page.getByRole("button", { name: "Dismiss message" }).all()) {
    await button.click({ timeout: 1000 }).catch(() => undefined);
  }
  await page.evaluate(axeSource);
  const violations: string[] = await page.evaluate(async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const axe = (window as any).axe;
    const result = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] },
    });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return result.violations.map((v: any) => `${v.id} (${v.impact}): ${v.help} -> ${v.nodes.slice(0, 3).map((n: any) => n.target.join(" ")).join(" | ")}`);
  });
  expect(violations, `accessibility violations on ${label}`).toEqual([]);
}
