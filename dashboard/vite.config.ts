import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";

const here = dirname(fileURLToPath(import.meta.url));
const KEY_PATH = resolve(here, "..", "private", "api_key.txt");

/**
 * The API is owner-only and rejects an unauthenticated request (AGENTS.md rule
 * 70). The proxy adds the key here rather than the dashboard doing it, so no
 * component changes and — the reason this is the right layer — <img> and
 * <video> tags get it too, which they could not if it were a fetch header.
 *
 * The key is never exposed to the browser: it is read in the Vite config,
 * which runs in Node. Mirrors agents/auth.get_or_create_key(), including the
 * exclusive create, so whichever of the two starts first on a cold machine
 * makes the key and the other reads it instead of clobbering it.
 */
function apiKey(): string {
  const fromEnv = (process.env.DASHBOARD_API_KEY ?? "").trim();
  if (fromEnv) return fromEnv;
  if (existsSync(KEY_PATH)) {
    const existing = readFileSync(KEY_PATH, "utf8").trim();
    if (existing) return existing;
  }
  mkdirSync(dirname(KEY_PATH), { recursive: true });
  try {
    writeFileSync(KEY_PATH, randomBytes(32).toString("hex"), { flag: "wx" });
  } catch {
    /* the API created it first — read whatever landed on disk */
  }
  return readFileSync(KEY_PATH, "utf8").trim();
}

// All dashboard requests go to /api/... and are proxied to the FastAPI server
// on port 8765 (the /api prefix is stripped). This includes /api/outputs/<file>
// for generated bookmark images, so no CORS configuration is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8765",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
        headers: { "X-API-Key": apiKey() },
      },
    },
  },
});
