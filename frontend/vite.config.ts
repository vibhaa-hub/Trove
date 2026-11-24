/// <reference types="vitest" />
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = process.env.TROVE_BACKEND ?? "http://127.0.0.1:8000";
const API_PREFIXES = ["/api", "/auth", "/cases", "/skills", "/health", "/ready", "/connectors", "/integrations"];

/** In dev, serve /nominee and /reviewer like the Python server does in production. */
function prettyPages(): Plugin {
  return {
    name: "trove-pretty-pages",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const path = req.url?.split("?")[0];
        if (path === "/nominee" || path === "/reviewer") req.url = `${path}.html`;
        next();
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), prettyPages()],
  build: {
    outDir: resolve(__dirname, "../trove_agent/web"),
    emptyOutDir: true,
    // keeps the build compatible with the server's strict CSP (no inline scripts)
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
    rollupOptions: {
      input: {
        owner: resolve(__dirname, "index.html"),
        nominee: resolve(__dirname, "nominee.html"),
        reviewer: resolve(__dirname, "reviewer.html"),
      },
      output: {
        manualChunks: (id) => (id.includes("node_modules") ? "vendor" : id.includes("/src/shared/") ? "shared" : undefined),
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      API_PREFIXES.map((prefix) => [
        prefix,
        {
          target: BACKEND,
          changeOrigin: true,
          // the backend pins writes to its own origin; present the proxied request as same-origin
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyReq) => proxyReq.setHeader("origin", BACKEND));
          },
        },
      ]),
    ),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
