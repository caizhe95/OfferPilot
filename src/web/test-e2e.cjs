const { spawn } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");

const root = __dirname;
const port = Number(process.env.PLAYWRIGHT_WEB_PORT || 3000);
const baseUrl = `http://127.0.0.1:${port}`;

function waitForServer(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tryRequest = () => {
      const request = http.get(baseUrl, (response) => {
        response.resume();
        resolve();
      });
      request.setTimeout(1_000, () => request.destroy());
      request.on("error", () => {
        if (Date.now() >= deadline) {
          reject(new Error(`Next.js test server did not become ready within ${timeoutMs}ms.`));
          return;
        }
        setTimeout(tryRequest, 200);
      });
    };
    tryRequest();
  });
}

function waitForExit(child, timeoutMs) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

async function stopServer(server) {
  if (server.exitCode !== null) return;
  server.kill("SIGTERM");
  await waitForExit(server, 5_000);
  if (server.exitCode === null) server.kill("SIGKILL");
}

function run(command, args, environment) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: root, env: environment, stdio: "inherit", windowsHide: true });
    child.once("error", reject);
    child.once("exit", (code) => resolve(code ?? 1));
  });
}

async function main() {
  const environment = { ...process.env, PLAYWRIGHT_WEB_PORT: String(port) };
  const server = spawn(
    process.execPath,
    ["node_modules/next/dist/bin/next", "dev", "--hostname", "127.0.0.1", "--port", String(port)],
    { cwd: root, env: environment, stdio: "inherit", windowsHide: true },
  );

  try {
    await waitForServer(60_000);
    process.exitCode = await run(process.execPath, ["node_modules/@playwright/test/cli.js", "test", ...process.argv.slice(2)], environment);
  } finally {
    await stopServer(server);
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
