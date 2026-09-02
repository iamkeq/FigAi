import { afterEach, describe, expect, test } from "bun:test";
import { DateTime } from "luxon";
import {
  type Agent,
  type AgentResult,
  AgentRunError,
  ProviderError,
} from "../src/agent/openrouter.ts";
import { parseConfig } from "../src/config.ts";
import { MemoryRepository } from "../src/db/memories.ts";
import { UserPreferenceRepository } from "../src/db/preferences.ts";
import { ReminderRepository } from "../src/db/reminders.ts";
import { SkillRepository } from "../src/db/skills.ts";
import { ScheduledTaskRunError, SlackScheduledTaskRunner } from "../src/reminders/agent-runner.ts";
import { SlackAuthorizer } from "../src/slack/authorization.ts";
import type { SlackClient } from "../src/slack/client.ts";
import type { Clock, RuntimeContext } from "../src/types.ts";
import { context, testDatabase } from "./helpers.ts";

const open: ReturnType<typeof testDatabase>[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

const config = parseConfig({
  SLACK_BOT_TOKEN: "xoxb-test",
  SLACK_APP_TOKEN: "xapp-test",
  OPENROUTER_API_KEY: "sk-or-v1-test",
  OWNER_USER_ID: "UOWNER",
  ALLOWED_CHANNEL_IDS: "C123",
  MATTGPT_DATA_DIR: "/tmp/mattgpt-scheduled-agent-test",
});

function result(text = "Two priorities"): AgentResult {
  return {
    text,
    model: "test/current-model",
    latencyMs: 20,
    promptTokens: 10,
    completionTokens: 5,
    totalTokens: 15,
    reportedCost: 0.001,
    tools: ["brain_search"],
    writeReceipts: [],
    images: [],
  };
}

function fakeSlack(): {
  client: SlackClient;
  members: string[];
  posts: string[];
  statuses: string[];
} {
  const members = ["U123"];
  const posts: string[] = [];
  const statuses: string[] = [];
  return {
    members,
    posts,
    statuses,
    client: {
      filesUploadV2: async () => ({ ok: true }),
      auth: { test: async () => ({ ok: true }) },
      users: {
        info: async ({ user }) => ({
          ok: true,
          user: {
            id: user,
            team_id: "T123",
            tz: "America/Chicago",
            profile: { display_name: "Current Name" },
          },
        }),
      },
      conversations: {
        info: async () => ({ ok: true }),
        members: async () => ({ ok: true, members, response_metadata: { next_cursor: "" } }),
        replies: async () => ({ ok: true, messages: [] }),
      },
      chat: {
        postMessage: async ({ text }) => {
          posts.push(text);
          return { ok: true };
        },
      },
      assistant: {
        threads: {
          setStatus: async ({ status }) => {
            statuses.push(status);
            return { ok: true };
          },
        },
      },
      reactions: {
        add: async () => ({ ok: true }),
        remove: async () => ({ ok: true }),
      },
    },
  };
}

function fixedClock(): Clock {
  return { now: () => new Date("2026-08-24T13:00:00Z") };
}

function createTask(reminders: ReminderRepository, overrides: Partial<RuntimeContext> = {}) {
  return reminders.create({
    context: context(overrides),
    text: "Read my current to-do list and recommend two priorities",
    firstRun: DateTime.fromISO("2099-01-01T09:00:00", { zone: "America/New_York" }),
    recurrence: "daily",
    kind: "agent_task",
    notificationTitle: "Daily Priority Brief",
    presentationInstructions:
      "List two priorities in ranked order and give one concise reason for each.",
    now: DateTime.fromISO("2026-01-01T00:00:00Z").toMillis(),
  });
}

describe("scheduled agent runner", () => {
  test("uses current authorization, memories, skills, time, and a fresh standalone command", async () => {
    const db = testDatabase();
    open.push(db);
    const slack = fakeSlack();
    const memories = new MemoryRepository(db);
    memories.save({
      scopeType: "channel",
      scopeId: "C123",
      text: "To Do contains ship the release and review invoices",
      actorUserId: "U123",
    });
    new UserPreferenceRepository(db).set({
      workspaceId: "T123",
      userId: "U123",
      values: { language: "Spanish", verbosity: "concise" },
    });
    const reminders = new ReminderRepository(db);
    const task = createTask(reminders);
    let captured:
      | { messages: Array<{ role: string; content: unknown }>; context: RuntimeContext }
      | undefined;
    const agent = {
      loadingStatus: async () => [],
      run: async (input: {
        messages: Array<{ role: string; content: unknown }>;
        context: RuntimeContext;
      }) => {
        captured = input;
        return result();
      },
    } as unknown as Agent;
    const runner = new SlackScheduledTaskRunner(
      config,
      db,
      slack.client,
      new SlackAuthorizer(slack.client, config, "T123"),
      memories,
      new SkillRepository(db),
      agent,
      "UBOT",
      fixedClock(),
      async () => {},
    );

    const outcome = await runner.run(task);
    expect(outcome).toEqual({ text: "Two priorities", writePerformed: false, attempts: 1 });
    expect(captured?.context).toMatchObject({
      requesterId: "U123",
      requesterName: "Current Name",
      timezone: "America/Chicago",
      isOwner: false,
    });
    const system = String(captured?.messages[0]?.content);
    expect(system).toContain("2026-08-24T08:00:00.000-05:00");
    expect(system).toContain("To Do contains ship the release");
    expect(system).toContain("language: Spanish");
    expect(system).toContain("verbosity: concise");
    expect(system).toContain("Brain Librarian");
    expect(String(captured?.messages[1]?.content)).toContain(task.text);
    expect(String(captured?.messages[1]?.content)).toContain("daily recurring scheduled task");
    expect(String(captured?.messages[1]?.content)).toContain("self-contained scheduled update");
    expect(String(captured?.messages[1]?.content)).toContain(
      "Execute its ordered steps now instead of rescheduling them",
    );
    expect(String(captured?.messages[1]?.content)).toContain(
      "create the directive with activation=now and no starts_at",
    );
    expect(String(captured?.messages[1]?.content)).toContain("never begin with a conversational");
    expect(String(captured?.messages[1]?.content)).toContain("Daily Priority Brief");
    expect(String(captured?.messages[1]?.content)).toContain("List two priorities in ranked order");
    expect(String(captured?.messages[1]?.content)).toContain("Do not repeat it in your result");
    expect(String(captured?.messages[1]?.content)).not.toContain("Slack thread history");
    expect(db.raw.query("SELECT model, total_tokens, status FROM interactions").get()).toEqual({
      model: "test/current-model",
      total_tokens: 15,
      status: "ok",
    });
    expect(slack.statuses[0]).toBe("is reading");
    expect(slack.statuses.at(-1)).toBe("");
  });

  test("bypasses cached membership when revalidating a scheduled DM", async () => {
    const db = testDatabase();
    open.push(db);
    const slack = fakeSlack();
    const authorizer = new SlackAuthorizer(slack.client, config, "T123");
    expect(
      (
        await authorizer.authorize({
          userId: "U123",
          channelId: "D123",
          surface: "dm",
        })
      ).allowed,
    ).toBeTrue();
    slack.members.splice(0);
    let called = false;
    const runner = new SlackScheduledTaskRunner(
      config,
      db,
      slack.client,
      authorizer,
      new MemoryRepository(db),
      new SkillRepository(db),
      {
        loadingStatus: async () => [],
        run: async () => {
          called = true;
          return result();
        },
      } as unknown as Agent,
      "UBOT",
      fixedClock(),
      async () => {},
    );
    const task = createTask(new ReminderRepository(db), {
      surface: "dm",
      channelId: "D123",
      threadTs: "200.000",
    });
    try {
      await runner.run(task);
      throw new Error("Expected authorization failure");
    } catch (error) {
      expect(error).toBeInstanceOf(ScheduledTaskRunError);
      expect((error as ScheduledTaskRunError).authorizationDenied).toBeTrue();
    }
    expect(called).toBeFalse();
  });

  test("preserves an explicit silent-success outcome without output", async () => {
    const db = testDatabase();
    open.push(db);
    const slack = fakeSlack();
    const task = createTask(new ReminderRepository(db));
    let scheduledPrompt = "";
    const runner = new SlackScheduledTaskRunner(
      config,
      db,
      slack.client,
      new SlackAuthorizer(slack.client, config, "T123"),
      new MemoryRepository(db),
      new SkillRepository(db),
      {
        loadingStatus: async () => [],
        run: async (input: { messages: Array<{ content: unknown }> }) => {
          scheduledPrompt = String(input.messages[1]?.content);
          return {
            ...result(""),
            suppressDelivery: true,
            tools: ["complete_scheduled_task_silently"],
          };
        },
      } as unknown as Agent,
      "UBOT",
      fixedClock(),
      async () => {},
    );
    expect(await runner.run(task)).toEqual({
      text: "",
      suppressDelivery: true,
      writePerformed: false,
      attempts: 1,
    });
    expect(scheduledPrompt).toContain("call complete_scheduled_task_silently");
    expect(slack.posts).toEqual([]);
  });

  test("retries one transient provider failure but never retries after a successful write", async () => {
    const db = testDatabase();
    open.push(db);
    const slack = fakeSlack();
    const task = createTask(new ReminderRepository(db));
    let attempts = 0;
    const sleeps: number[] = [];
    const retrying = new SlackScheduledTaskRunner(
      config,
      db,
      slack.client,
      new SlackAuthorizer(slack.client, config, "T123"),
      new MemoryRepository(db),
      new SkillRepository(db),
      {
        loadingStatus: async () => [],
        run: async () => {
          attempts += 1;
          if (attempts === 1) throw new ProviderError("temporary", 503, true);
          return result("Recovered");
        },
      } as unknown as Agent,
      "UBOT",
      fixedClock(),
      async (milliseconds) => {
        sleeps.push(milliseconds);
      },
    );
    expect(await retrying.run(task)).toMatchObject({ text: "Recovered", attempts: 2 });
    expect(sleeps).toEqual([2_000]);

    let writeAttempts = 0;
    const noReplay = new SlackScheduledTaskRunner(
      config,
      db,
      slack.client,
      new SlackAuthorizer(slack.client, config, "T123"),
      new MemoryRepository(db),
      new SkillRepository(db),
      {
        loadingStatus: async () => [],
        run: async () => {
          writeAttempts += 1;
          throw new AgentRunError(new ProviderError("temporary", 503, true), [
            "Private memory saved",
          ]);
        },
      } as unknown as Agent,
      "UBOT",
      fixedClock(),
      async () => {},
    );
    await expect(noReplay.run(task)).rejects.toBeInstanceOf(ScheduledTaskRunError);
    expect(writeAttempts).toBe(1);
  });
});
