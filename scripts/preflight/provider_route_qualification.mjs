import { spawnSync } from "node:child_process";

const result = spawnSync(
  ".venv/bin/python",
  ["scripts/preflight/provider_route_qualification.py", "--execute", ...process.argv.slice(2)],
  { stdio: "inherit", env: process.env },
);

if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
