import { cpSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";

// Generated build assets only; user source files are never modified.
const build = process.env.LAB_NEXT_DIST_DIR || ".next";
cpSync(`${build}/static`, `${build}/standalone/${build}/static`, {
  recursive: true,
});
if (existsSync("public"))
  cpSync("public", `${build}/standalone/public`, { recursive: true });
const child = spawn(process.execPath, [`${build}/standalone/server.js`], {
  stdio: "inherit",
  env: { ...process.env, HOSTNAME: process.env.LAB_BIND_HOST || "127.0.0.1" },
});
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => child.kill(sig));
child.on("exit", (code) => process.exit(code ?? 1));
