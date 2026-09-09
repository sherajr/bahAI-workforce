/**
 * `npm run dev` -- bring the whole bahAI Workforce app up, in order.
 *
 * Before this existed, `npm run dev` was bare `vite`: it started the dashboard
 * and nothing else, so whether anything worked depended on whether the API
 * happened to still be alive from the last Windows logon (the Scheduled Task's
 * only trigger). When it was not, the dashboard came up looking normal and
 * every panel sat there failing -- "it takes forever to load and now it says the
 * backend isn't running".
 *
 * So the order here is the point:
 *   1. the API is made to answer on :8765 (scripts/ensure_backend.ps1)
 *   2. the things it depends on are checked and REPORTED, never silently
 *      assumed (Ollama for local models, the tunnel for WhatsApp)
 *   3. only then does Vite start
 *
 * Nothing is started that Sheraj did not ask for, and nothing is stopped on the
 * way out: Ctrl+C ends the dashboard only. The API keeps running on purpose --
 * Abigail answers WhatsApp through it whether or not a browser is open, so
 * shutting it down with the dev server would take her offline (AGENTS.md rules
 * 26-28).
 *
 * Run `npm run dev:web` for the old behaviour (Vite alone, no backend step).
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dashboard = resolve(here, "..");
const root = resolve(dashboard, "..");

const API_PORT = 8765;
const WEB_PORT = 5173;

const bold = (s) => `\x1b[1m${s}\x1b[0m`;
const dim = (s) => `\x1b[2m${s}\x1b[0m`;
const red = (s) => `\x1b[31m${s}\x1b[0m`;
const yellow = (s) => `\x1b[33m${s}\x1b[0m`;

/** A service is up only if it ANSWERS. See ensure_backend.ps1 on why. */
async function answers(url, ms = 3000) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(ms) });
    return r.ok;
  } catch {
    return false;
  }
}

function powershell(args, opts = {}) {
  return spawnSync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", ...args],
    { cwd: root, ...opts },
  );
}

/**
 * Step 1 -- the API. This is the one step that can fail the startup, because
 * without it the dashboard shows nothing but errors.
 */
function ensureBackend() {
  console.log(`${bold("1/4")}  Backend API (port ${API_PORT})`);
  if (process.platform !== "win32") {
    console.log(yellow("     Not Windows -- skipping. Start the API yourself."));
    return true;
  }
  const script = resolve(root, "scripts", "ensure_backend.ps1");
  if (!existsSync(script)) {
    console.log(red(`     Missing ${script} -- cannot start the API.`));
    return false;
  }
  const r = powershell(["-File", script], { stdio: "inherit" });
  return r.status === 0;
}

/**
 * Steps 2 and 3 -- reported, never started. Ollama is a tray app and the tunnel
 * is its own Scheduled Task; both are Sheraj's to run, and a wrong guess here
 * would be a second copy of something. What matters is that he can see which
 * one is down BEFORE a run fails halfway through and looks like a code fault.
 */
async function reportOllama() {
  const up = await answers("http://127.0.0.1:11434/api/tags", 3000);
  console.log(`${bold("2/4")}  Ollama, local models (port 11434)`);
  console.log(
    up
      ? "     ready"
      : yellow("     not running -- local agents (Librarian, Scribe, Reviewer) will fail. Start the Ollama app."),
  );
}

function reportTunnel() {
  console.log(`${bold("3/4")}  Cloudflare Tunnel (WhatsApp)`);
  if (process.platform !== "win32") return;
  const r = powershell([
    "-Command",
    "@(Get-Process cloudflared -ErrorAction SilentlyContinue).Count",
  ]);
  const running = parseInt(String(r.stdout ?? "").trim(), 10) > 0;
  console.log(
    running
      ? "     running"
      : dim('     not running -- the dashboard is fine; WhatsApp messages will not arrive.\n' +
          '     Start it with: Start-ScheduledTask -TaskName "bahAI Secretary Tunnel"'),
  );
}

/**
 * Step 4 -- Vite. The leftovers are cleared FIRST so the dashboard comes back on
 * its usual port; see scripts/clear_stale_dashboards.ps1 for what that fixes.
 */
function startVite() {
  console.log(`${bold("4/4")}  Dashboard (port ${WEB_PORT})`);
  if (process.platform === "win32") {
    const cleaner = resolve(root, "scripts", "clear_stale_dashboards.ps1");
    if (existsSync(cleaner)) powershell(["-File", cleaner], { stdio: "inherit" });
  }
  const bin = resolve(dashboard, "node_modules", "vite", "bin", "vite.js");
  const child = spawn(process.execPath, [bin, ...process.argv.slice(2)], {
    cwd: dashboard,
    stdio: "inherit",
  });
  // Ctrl+C reaches the child through the console's own signal; just follow it
  // out, and leave the API running (see the file header).
  const bye = () => {};
  process.on("SIGINT", bye);
  process.on("SIGTERM", bye);
  child.on("exit", (code, signal) => {
    process.exit(signal ? 0 : (code ?? 0));
  });
}

async function main() {
  console.log(`\n${bold("bahAI Workforce")} -- starting up\n`);

  const backendOk = ensureBackend();
  await reportOllama();
  reportTunnel();

  if (!backendOk) {
    // Vite still starts: a dashboard that loads and says what is wrong beats a
    // blank terminal. But the reason is stated here in full, not left to be
    // inferred from failing panels.
    console.log(
      red("\nThe backend did not come up. The dashboard will load but every panel will fail.") +
        red("\nCheck logs/api.err.log (printed above), then try again.\n"),
    );
  }

  console.log("");
  startVite();
}

main();
