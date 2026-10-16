import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { once } from "node:events";

export async function startRealServer() {
  const child = spawn(
    process.env.FACTORY_TEST_PYTHON || "python",
    ["scripts/browser_http_fixture.py"],
    {
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        PATH: process.env.PATH,
        PYTHONPATH: process.env.PYTHONPATH || "",
        PYTHONUNBUFFERED: "1",
      },
    },
  );
  let errors = "";
  const responses = [];
  child.stderr.on("data", (data) => {
    errors = (errors + data).slice(-4000);
  });
  const lines = createInterface({ input: child.stdout });
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error("HTTP fixture did not start: " + errors));
    }, 12000);
    const failed = (code) => {
      clearTimeout(timer);
      reject(new Error("HTTP fixture exited " + code + ": " + errors));
    };
    child.once("exit", failed);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    lines.on("line", (line) => {
      if (line.startsWith("HTTP_BROWSER_RESPONSE ")) {
        responses.push(JSON.parse(line.slice("HTTP_BROWSER_RESPONSE ".length)));
        return;
      }
      if (!line.startsWith("HTTP_BROWSER_FIXTURE_READY ")) return;
      clearTimeout(timer);
      child.off("exit", failed);
      resolve(JSON.parse(line.slice("HTTP_BROWSER_FIXTURE_READY ".length)));
    });
  });
  return {
    endpoint: "http://127.0.0.1:" + ready.port,
    projectId: ready.project_id,
    responses,
    async stop() {
      lines.close();
      if (child.exitCode === null) {
        const stopped = once(child, "exit");
        child.kill("SIGTERM");
        await stopped;
      }
    },
  };
}
