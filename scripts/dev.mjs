/* Starts the Vite dev server, reclaiming port 5999 if a previous run left one
   behind. strictPort is deliberate in vite.config.ts: the server must never
   drift onto another port, because Chrome blocks some of them outright. */

import { execFileSync, spawn } from "node:child_process";
import { createConnection } from "node:net";

const PORT = 5999;
const WINDOWS = process.platform === "win32";
// Vite binds localhost, which resolves to ::1 on Windows, so probe both stacks.
const HOSTS = ["127.0.0.1", "::1"];

function probe(host) {
  return new Promise((resolve) => {
    const socket = createConnection({ port: PORT, host });
    socket.setTimeout(700);
    const done = (answer) => { socket.destroy(); resolve(answer); };
    socket.on("connect", () => done(true));
    socket.on("timeout", () => done(false));
    socket.on("error", () => resolve(false));
  });
}

async function inUse() {
  for (const host of HOSTS) {
    if (await probe(host)) return true;
  }
  return false;
}

function run(file, args) {
  try {
    return execFileSync(file, args, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return "";
  }
}

function powershell(command) {
  return run("powershell", ["-NoProfile", "-Command", command]).trim();
}

function listenerPid() {
  if (WINDOWS) {
    const pid = powershell(
      `Get-NetTCPConnection -LocalPort ${PORT} -State Listen -ErrorAction SilentlyContinue |` +
        " Select-Object -First 1 -ExpandProperty OwningProcess",
    );
    return pid ? Number(pid) : null;
  }
  const pid = run("lsof", ["-ti", `tcp:${PORT}`, "-sTCP:LISTEN"]).trim().split(/\s+/)[0];
  return pid ? Number(pid) : null;
}

function commandLine(pid) {
  if (WINDOWS) {
    return powershell(`(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`);
  }
  return run("ps", ["-o", "command=", "-p", String(pid)]).trim();
}

function stop(pid) {
  if (WINDOWS) run("taskkill", ["/PID", String(pid), "/T", "/F"]);
  else process.kill(pid, "SIGTERM");
}

async function waitForFree() {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (!(await inUse())) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function reclaim() {
  if (!(await inUse())) return true;
  const pid = listenerPid();
  if (!pid) {
    console.error(`Port ${PORT} is busy but the owning process could not be identified.`);
    return false;
  }
  const command = commandLine(pid);
  if (!/vite|node/i.test(command)) {
    console.error(`Port ${PORT} is held by PID ${pid}, which is not a dev server:`);
    console.error(`  ${command || "unknown command"}`);
    console.error("Stop that process yourself, or change the port in frontend/vite.config.ts.");
    return false;
  }
  console.log(`Port ${PORT} was still held by an earlier dev server (PID ${pid}). Stopping it.`);
  stop(pid);
  if (await waitForFree()) return true;
  console.error(`Port ${PORT} did not free up. Stop PID ${pid} manually and try again.`);
  return false;
}

if (!(await reclaim())) process.exit(1);

const extra = process.argv.slice(2);
const child = WINDOWS
  ? spawn(["npm", "--prefix", "frontend", "run", "dev", ...(extra.length ? ["--", ...extra] : [])].join(" "), {
      stdio: "inherit",
      shell: true,
    })
  : spawn("npm", ["--prefix", "frontend", "run", "dev", ...(extra.length ? ["--", ...extra] : [])], {
      stdio: "inherit",
    });
child.on("exit", (code) => process.exit(code ?? 0));
