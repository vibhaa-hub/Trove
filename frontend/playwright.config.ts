import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against the real Python server and a fresh temporary database.
 *   npm run e2e         headless
 *   npm run e2e:watch   opens Edge and runs slowly so you can watch every step
 * Uses the installed Microsoft Edge (channel "msedge"), so no browser download is needed.
 */
const here = dirname(fileURLToPath(import.meta.url));
const PORT = 8766;
const runId = (process.env.TROVE_E2E_RUN ??= String(Date.now()));
const workDir = join(tmpdir(), `trove-e2e-${runId}`);
const venvPython = resolve(here, "..", process.platform === "win32" ? "venv/Scripts/python.exe" : "venv/bin/python");
const python = process.env.TROVE_PYTHON ?? (existsSync(venvPython) ? venvPython : "python");

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: "msedge",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1360, height: 900 },
  },
  projects: [
    { name: "e2e", use: { headless: true } },
    { name: "watch", use: { headless: false, launchOptions: { slowMo: 450 } } },
  ],
  webServer: {
    command: `"${python}" -m trove_agent --db "${join(workDir, "e2e.db")}" --port ${PORT}`,
    cwd: resolve(here, ".."),
    url: `http://127.0.0.1:${PORT}/ready`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      ...process.env,
      TROVE_VAULT_DIR: join(workDir, "vault"),
      TROVE_ENCODER: "hashing",
      TROVE_NOTIFIER: "dev",
      TROVE_ENV: "development",
    } as Record<string, string>,
  },
});
