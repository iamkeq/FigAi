import { afterEach, describe, expect, test } from "bun:test";
import { DateTime } from "luxon";
import { ReminderRepository } from "../src/db/reminders.ts";
import { ScheduledTaskRunError } from "../src/reminders/agent-runner.ts";
import { ReminderScheduler } from "../src/reminders/scheduler.ts";
import type { SlackClient } from "../src/slack/client.ts";
import type { Clock } from "../src/types.ts";
import { context, testDatabase } from "./helpers.ts";

const open: ReturnType<typeof testDatabase>[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

function slackWithPost(post: SlackClient["chat"]["postMessage"]): SlackClient {
  return {
    filesUploadV2: async () => ({ ok: true }),
    auth: { test: async () => ({ ok: true }) },
    users: { info: async () => ({ ok: true }) },
    conversations: {
      info: async () => ({ ok: true }),
      members: async () => ({ ok: true, members: [] }),
      replies: async () => ({ ok: true, messages: [] }),
    },
    chat: { postMessage: post },
    assistant: { threads: { setStatus: async () => ({ ok: true }) } },
    reactions: { add: async () => ({ ok: true }), remove: async () => ({ ok: true }) },
  };
}

class MutableClock implements Clock {
  constructor(public value: number) {}
  now(): Date {
    return new Date(this.value);
  }
}

describe("reminder scheduler", () => {
  test("recovers an overdue one-time reminder once and marks it late", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00", { zone: "America/New_York" });
    const row = reminders.create({
      context: context(),
      text: "Ship it",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    const posts: Array<{ text: string; client_msg_id?: string }> = [];
    const clock = new MutableClock(base.plus({ hours: 2 }).toMillis());
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async (args) => {
        posts.push(args);
        return { ok: true };
      }),
      clock,
      15_000,
      async () => {},
    );
    await scheduler.poll();
    await scheduler.poll();
    expect(posts).toHaveLength(1);
    expect(posts[0]?.text).toContain("late reminder");
    expect(posts[0]?.text).toStartWith("<@U123>");
    expect(posts[0]?.client_msg_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(reminders.get(row.id)?.completed_at).not.toBeNull();
    expect(db.raw.query("SELECT status, late FROM reminder_deliveries").all()).toEqual([
      { status: "delivered", late: 1 },
    ]);
  });

  test("sends at most one missed recurring occurrence and advances into the future", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-03-01T09:00:00", { zone: "America/New_York" });
    const row = reminders.create({
      context: context({ surface: "dm", channelId: "D1" }),
      text: "Daily check",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "daily",
      now: base.toMillis(),
    });
    const posts: string[] = [];
    const clock = new MutableClock(base.plus({ days: 4 }).toMillis());
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      clock,
      15_000,
      async () => {},
    );
    await scheduler.poll();
    expect(posts).toEqual(["⏰ Daily check"]);
    expect(reminders.get(row.id)?.next_run_at).toBeGreaterThan(clock.value);
  });

  test("routes reminders to a top-level channel message or requester DM", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    reminders.create({
      context: context(),
      text: "Channel notice",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      delivery: "channel",
      now: base.toMillis(),
    });
    reminders.create({
      context: context(),
      text: "Private notice",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      delivery: "dm",
      now: base.toMillis(),
    });
    const posts: Array<{
      channel: string;
      thread_ts?: string;
      text: string;
      client_msg_id?: string;
    }> = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async (args) => {
        posts.push(args);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 1 }).toMillis()),
      15_000,
      async () => {},
    );
    await scheduler.poll();
    expect(posts).toEqual([
      {
        channel: "C123",
        text: "<@U123> ⏰ Channel notice",
        client_msg_id: expect.any(String),
      },
      {
        channel: "U123",
        text: "⏰ Private notice",
        client_msg_id: expect.any(String),
      },
    ]);
  });

  test("honors leases and permits recovery after expiry", () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    reminders.create({
      context: context(),
      text: "Lease",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    const due = base.plus({ minutes: 2 }).toMillis();
    expect(reminders.leaseDue(due, 60_000)).toHaveLength(1);
    expect(reminders.leaseDue(due + 1, 60_000)).toHaveLength(0);
    expect(reminders.leaseDue(due + 60_001, 60_000)).toHaveLength(1);
  });

  test("retries delivery three times and records the actual attempt count", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const row = reminders.create({
      context: context(),
      text: "Retry",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    let attempts = 0;
    const sleeps: number[] = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async () => {
        attempts += 1;
        if (attempts < 3) throw new Error("temporary");
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 2 }).toMillis()),
      15_000,
      async (milliseconds) => {
        sleeps.push(milliseconds);
      },
    );
    await scheduler.poll();
    expect(attempts).toBe(3);
    expect(sleeps).toEqual([500, 1000]);
    expect(
      db.raw
        .query("SELECT attempt_count FROM reminder_deliveries WHERE reminder_id = ?")
        .get(row.id),
    ).toEqual({
      attempt_count: 3,
    });
  });

  test("records terminal cycle failures and enforces cancellation ownership", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const row = reminders.create({
      context: context(),
      text: "Fail",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async () => {
        throw new Error("Slack down");
      }),
      new MutableClock(base.plus({ minutes: 2 }).toMillis()),
      15_000,
      async () => {},
    );
    await scheduler.poll();
    expect(
      db.raw
        .query("SELECT status, attempt_count FROM reminder_deliveries WHERE reminder_id = ?")
        .get(row.id),
    ).toEqual({
      status: "failed",
      attempt_count: 3,
    });
    expect(() =>
      reminders.cancel({
        id: row.id,
        actorUserId: "UOTHER",
        ownerUserId: "UOWNER",
        context: context({ requesterId: "UOTHER" }),
      }),
    ).toThrow("permission");
    expect(
      reminders.cancel({
        id: row.id,
        actorUserId: "UOWNER",
        ownerUserId: "UOWNER",
        context: context({ requesterId: "UOWNER" }),
      }),
    ).toBeTrue();
  });

  test("reconciles a failed delivery record when a later retry succeeds", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const row = reminders.create({
      context: context(),
      text: "Eventually",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    const clock = new MutableClock(base.plus({ minutes: 2 }).toMillis());
    let available = false;
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async () => {
        if (!available) throw new Error("Slack down");
        return { ok: true };
      }),
      clock,
      15_000,
      async () => {},
    );
    await scheduler.poll();
    available = true;
    clock.value += 3_600_001;
    await scheduler.poll();
    expect(
      db.raw
        .query("SELECT status, attempt_count, error FROM reminder_deliveries WHERE reminder_id = ?")
        .get(row.id),
    ).toEqual({
      status: "delivered",
      attempt_count: 4,
      error: null,
    });
  });

  test("caps users at 25 active reminders and 10 recurring reminders", () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    for (let index = 0; index < 10; index += 1) {
      reminders.create({
        context: context(),
        text: `recurring ${index}`,
        firstRun: base.plus({ days: index + 1 }),
        recurrence: "daily",
        now: base.toMillis(),
      });
    }
    expect(() =>
      reminders.create({
        context: context(),
        text: "recurring 11",
        firstRun: base.plus({ days: 20 }),
        recurrence: "weekly",
        now: base.toMillis(),
      }),
    ).toThrow("10 recurring");
    for (let index = 0; index < 15; index += 1) {
      reminders.create({
        context: context(),
        text: `once ${index}`,
        firstRun: base.plus({ days: index + 30 }),
        recurrence: "once",
        now: base.toMillis(),
      });
    }
    expect(() =>
      reminders.create({
        context: context(),
        text: "number 26",
        firstRun: base.plus({ days: 100 }),
        recurrence: "once",
        now: base.toMillis(),
      }),
    ).toThrow("25 active");
  });

  test("delivers cheap reminders before agent tasks and journals the agent result", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const task = reminders.create({
      context: context(),
      text: "Inspect current priorities",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    reminders.create({
      context: context(),
      text: "Static notification",
      firstRun: base.plus({ minutes: 2 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    const order: string[] = [];
    const clock = new MutableClock(base.plus({ minutes: 3 }).toMillis());
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        order.push(`post:${text}`);
        return { ok: true };
      }),
      clock,
      15_000,
      async () => {},
      {
        run: async () => {
          order.push("agent");
          return { text: "Current answer", writePerformed: false, attempts: 1 };
        },
      },
    );
    await scheduler.poll();
    await scheduler.drain();
    expect(order[0]).toContain("Static notification");
    expect(order.slice(1)).toEqual(["agent", "post:<@U123> _(late)_ Current answer"]);
    expect(reminders.get(task.id)?.completed_at).not.toBeNull();
    expect(
      db.raw
        .query("SELECT status, response_text FROM agent_task_runs WHERE reminder_id = ?")
        .get(task.id),
    ).toEqual({ status: "delivered", response_text: "Current answer" });
  });

  test("delivers a durable ready result after restart without rerunning the agent", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const task = reminders.create({
      context: context({ surface: "dm", channelId: "D1" }),
      text: "Use current data",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    reminders.claimAgentRun(task, base.plus({ minutes: 1 }).toMillis());
    reminders.markAgentReady(
      task,
      "Recovered final answer",
      false,
      base.plus({ minutes: 1 }).toMillis(),
    );
    const posts: string[] = [];
    let agentRuns = 0;
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 2 }).toMillis()),
      15_000,
      async () => {},
      {
        run: async () => {
          agentRuns += 1;
          return { text: "wrong", writePerformed: false, attempts: 1 };
        },
      },
    );
    await scheduler.poll();
    await scheduler.drain();
    expect(agentRuns).toBe(0);
    expect(posts).toEqual(["_(late)_ Recovered final answer"]);
  });

  test("frames recurring agent results as standalone scheduled updates", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    reminders.create({
      context: context(),
      text: "Check the provider page",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "daily",
      kind: "agent_task",
      delivery: "channel",
      notificationTitle: "Daily Provider Watch",
      presentationInstructions:
        "State whether the listing changed, then give the current model names in one sentence.",
      now: base.toMillis(),
    });
    const posts: Array<Parameters<SlackClient["chat"]["postMessage"]>[0]> = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async (args) => {
        posts.push(args);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 1 }).toMillis()),
      15_000,
      async () => {},
      {
        run: async () => ({
          text: "Yes. The provider page still lists Ox Alpha.",
          writePerformed: false,
          attempts: 1,
        }),
      },
    );
    await scheduler.poll();
    await scheduler.drain();
    expect(posts).toEqual([
      {
        text: "<@U123> *Daily Provider Watch*\nThe provider page still lists Ox Alpha.",
        blocks: [
          {
            type: "markdown",
            text: "<@U123> *Daily Provider Watch*\nThe provider page still lists Ox Alpha.",
          },
        ],
        channel: "C123",
        client_msg_id: expect.any(String),
        unfurl_links: false,
        unfurl_media: false,
      },
    ]);
  });

  test("completes and durably recovers silent agent results without posting", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const fresh = reminders.create({
      context: context({ surface: "dm", channelId: "D1" }),
      text: "Say nothing when unchanged",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    const posts: string[] = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 2 }).toMillis()),
      15_000,
      async () => {},
      {
        run: async () => ({
          text: "",
          suppressDelivery: true,
          writePerformed: false,
          attempts: 1,
        }),
      },
    );
    await scheduler.poll();
    await scheduler.drain();
    expect(posts).toEqual([]);
    expect(reminders.get(fresh.id)?.completed_at).not.toBeNull();
    expect(
      db.raw
        .query(
          "SELECT status, response_text, suppress_delivery FROM agent_task_runs WHERE reminder_id = ?",
        )
        .get(fresh.id),
    ).toEqual({ status: "delivered", response_text: "", suppress_delivery: 1 });

    const recovered = reminders.create({
      context: context({ surface: "dm", channelId: "D1" }),
      text: "Remain quiet",
      firstRun: base.plus({ minutes: 3 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    reminders.claimAgentRun(recovered, base.plus({ minutes: 3 }).toMillis());
    reminders.markAgentReady(recovered, "", false, base.plus({ minutes: 3 }).toMillis(), true);
    let reruns = 0;
    const recoveryScheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 4 }).toMillis()),
      15_000,
      async () => {},
      {
        run: async () => {
          reruns += 1;
          return { text: "wrong", writePerformed: false, attempts: 1 };
        },
      },
    );
    await recoveryScheduler.poll();
    await recoveryScheduler.drain();
    expect(reruns).toBe(0);
    expect(posts).toEqual([]);
    expect(reminders.get(recovered.id)?.completed_at).not.toBeNull();
  });

  test("does not replay an interrupted agent task", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    const task = reminders.create({
      context: context(),
      text: "Potentially write something",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    reminders.claimAgentRun(task, base.plus({ minutes: 1 }).toMillis());
    let agentRuns = 0;
    const posts: string[] = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 2 }).toMillis()),
      15_000,
      async () => {},
      {
        run: async () => {
          agentRuns += 1;
          return { text: "wrong", writePerformed: false, attempts: 1 };
        },
      },
    );
    await scheduler.poll();
    await scheduler.drain();
    expect(agentRuns).toBe(0);
    expect(posts[0]).toContain("did not replay");
    expect(reminders.get(task.id)?.completed_at).not.toBeNull();
    expect(db.raw.query("SELECT status FROM reminder_deliveries").get()).toEqual({
      status: "failed",
    });
  });

  test("reports completed writes when a scheduled agent later fails", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    reminders.create({
      context: context({ surface: "dm", channelId: "D1" }),
      text: "Save the result",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    const posts: string[] = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      new MutableClock(base.plus({ minutes: 2 }).toMillis()),
      15_000,
      async () => {},
      {
        run: async () => {
          throw new ScheduledTaskRunError(
            new Error("provider failed"),
            ["Private memory saved"],
            1,
          );
        },
      },
    );
    await scheduler.poll();
    await scheduler.drain();
    expect(posts[0]).toContain("did not replay them");
    expect(posts[0]).toContain("✓ Private memory saved");
  });

  test("continues polling cheap reminders while an agent task is still running", async () => {
    const db = testDatabase();
    open.push(db);
    const reminders = new ReminderRepository(db);
    const base = DateTime.fromISO("2026-01-01T09:00:00Z");
    reminders.create({
      context: context(),
      text: "Long current-data task",
      firstRun: base.plus({ minutes: 1 }),
      recurrence: "once",
      kind: "agent_task",
      now: base.toMillis(),
    });
    const clock = new MutableClock(base.plus({ minutes: 2 }).toMillis());
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const posts: string[] = [];
    const scheduler = new ReminderScheduler(
      reminders,
      slackWithPost(async ({ text }) => {
        posts.push(text);
        return { ok: true };
      }),
      clock,
      15_000,
      async () => {},
      {
        run: async () => {
          await gate;
          return { text: "Agent finished", writePerformed: false, attempts: 1 };
        },
      },
    );
    await scheduler.poll();
    reminders.create({
      context: context(),
      text: "Deliver me immediately",
      firstRun: base.plus({ seconds: 30 }),
      recurrence: "once",
      now: base.toMillis(),
    });
    await scheduler.poll();
    expect(posts).toEqual(["<@U123> _(late reminder)_ ⏰ Deliver me immediately"]);
    release();
    await scheduler.drain();
    expect(posts.at(-1)).toContain("Agent finished");
  });
});
