/**
 * Vite configuration. In development the API runs separately (default
 * http://localhost:8000) and is proxied, so the app and the API share an
 * origin as they do in the container, where nginx serves both.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const pkg = JSON.parse(
  readFileSync(fileURLToPath(new URL("./package.json", import.meta.url)), "utf-8"),
) as { version: string };

const api = process.env.FDPINDEX_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/index": api,
      "/openapi.json": api,
      "/docs": api,
      "/healthz": api,
    },
  },
  build: {
    target: "es2022",
    sourcemap: true,
  },
});
