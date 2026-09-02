import { afterEach, describe, expect, test } from "bun:test";
import type { Agent } from "../src/agent/openrouter.ts";
import type { MattDatabase } from "../src/db/database.ts";
import { MemoryRepository } from "../src/db/memories.ts";
import { SkillRepository } from "../src/db/skills.ts";
import { WebUiServer } from "../src/webui/server.ts";
import { testDatabase } from "./helpers.ts";

const open: MattDatabase[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

function fakeAgent(run: Agent["run"]): Agent {
  return { run } as unknown as Agent;
}

function server(
  db: MattDatabase,
  agent: Agent,
  modelState = { value: "primary/model" },
  port = 34567 + Math.floor(Math.random() * 1000),
): WebUiServer {
  return new WebUiServer(
    port,
    agent,
    new MemoryRepository(db),
    new SkillRepository(db),
    {
      getPrimaryModel: () => modelState.value,
      setPrimaryModel: (model) => {
        modelState.value = model;
      },
      resolveModel: async (model) => (model === "vendor/new-model" ? "vendor/new-model" : null),
    },
    db,
    "primary/model",
    "UOWNER",
    "T123",
    "America/New_York",
  );
}

describe("web UI server", () => {
  test("serves the chat page and reports the current model", async () => {
    const db = testDatabase();
    open.push(db);
    const web = server(
      db,
      fakeAgent(async () => {
        throw new Error("unused");
      }),
    );
    web.start();
    try {
      const port = (web as unknown as { port: number }).port;
      const page = await fetch(`http://127.0.0.1:${port}/`);
      expect(page.status).toBe(200);
      expect(await page.text()).toContain("FigAi");

      const model = await fetch(`http://127.0.0.1:${port}/api/model`);
      expect(await model.json()).toEqual({ model: "primary/model" });
    } finally {
      await web.stop();
    }
  });

  test("runs a chat turn through the same agent pipeline and keeps history", async () => {
    const db = testDatabase();
    open.push(db);
    const calls: unknown[] = [];
    const web = server(
      db,
      fakeAgent(async (input) => {
        calls.push(input);
        return {
          text: "Hello from FigAi.",
          model: "primary/model",
          latencyMs: 1,
          promptTokens: 1,
          completionTokens: 1,
          totalTokens: 2,
          reportedCost: 0,
          tools: [],
          writeReceipts: [],
          images: [],
        };
      }),
    );
    web.start();
    try {
      const port = (web as unknown as { port: number }).port;
      const res = await fetch(`http://127.0.0.1:${port}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "Hi there" }),
      });
      expect(res.status).toBe(200);
      expect(await res.json()).toMatchObject({ text: "Hello from FigAi.", totalTokens: 2 });
      expect(calls).toHaveLength(1);
      const runInput = calls[0] as { context: { isOwner: boolean; requesterId: string } };
      expect(runInput.context).toMatchObject({ isOwner: true, requesterId: "UOWNER" });

      const history = await fetch(`http://127.0.0.1:${port}/api/messages`);
      expect(await history.json()).toEqual({
        messages: [
          { role: "user", text: "Hi there" },
          { role: "assistant", text: "Hello from FigAi." },
        ],
      });
    } finally {
      await web.stop();
    }
  });

  test("rejects an unknown model and resets to the configured default", async () => {
    const db = testDatabase();
    open.push(db);
    const modelState = { value: "primary/model" };
    const web = server(
      db,
      fakeAgent(async () => {
        throw new Error("unused");
      }),
      modelState,
    );
    web.start();
    try {
      const port = (web as unknown as { port: number }).port;
      const bad = await fetch(`http://127.0.0.1:${port}/api/model`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: "vendor/unknown" }),
      });
      expect(bad.status).toBe(400);

      const good = await fetch(`http://127.0.0.1:${port}/api/model`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: "vendor/new-model" }),
      });
      expect(await good.json()).toEqual({ model: "vendor/new-model", changed: true });
      expect(modelState.value).toBe("vendor/new-model");

      const reset = await fetch(`http://127.0.0.1:${port}/api/model`, { method: "DELETE" });
      expect(await reset.json()).toEqual({ model: "primary/model", reset: true });
      expect(modelState.value).toBe("primary/model");
    } finally {
      await web.stop();
    }
  });
});
