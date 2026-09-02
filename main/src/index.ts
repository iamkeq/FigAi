import { FigAiApp } from "./app.ts";
import { loadConfig } from "./config.ts";
import { errorMessage, log } from "./logger.ts";

async function main(): Promise<void> {
  const app = new FigAiApp(loadConfig());
  const shutdown = async (signal: string) => {
    log("info", "shutdown_requested", { signal });
    await app.stop();
    process.exit(0);
  };
  process.once("SIGTERM", () => void shutdown("SIGTERM"));
  process.once("SIGINT", () => void shutdown("SIGINT"));
  await app.start();
}

if (import.meta.main) {
  main().catch((error) => {
    log("error", "startup_failed", { error: errorMessage(error) });
    process.exit(1);
  });
}
