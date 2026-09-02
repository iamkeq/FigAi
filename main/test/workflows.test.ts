import { afterEach, describe, expect, test } from "bun:test";
import {
  ingressCandidates,
  type WorkflowPlan,
  WorkflowRepository,
  workflowPlanSchema,
} from "../src/db/workflows.ts";
import type { SlackClient } from "../src/slack/client.ts";
import { WorkflowEngine } from "../src/workflows/engine.ts";
import { context, testDatabase } from "./helpers.ts";

const open: ReturnType<typeof testDatabase>[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

function fakeSlack(): {
  client: SlackClient;
  posts: Array<Parameters<SlackClient["chat"]["postMessage"]>[0]>;
} {
  const posts: Array<Parameters<SlackClient["chat"]["postMessage"]>[0]> = [];
  return {
    posts,
    client: {
      filesUploadV2: async () => ({ ok: true }),
      auth: { test: async () => ({ ok: true }) },
      users: { info: async () => ({ ok: true }) },
      conversations: {
        info: async () => ({ ok: true }),
        members: async () => ({ ok: true, members: [] }),
        replies: async () => ({ ok: true, messages: [] }),
      },
      chat: {
        postMessage: async (args) => {
          posts.push(args);
          return { ok: true };
        },
      },
      assistant: { threads: { setStatus: async () => ({ ok: true }) } },
      reactions: {
        add: async () => ({ ok: true }),
        remove: async () => ({ ok: true }),
      },
    },
  };
}

const trimmerPlan: WorkflowPlan = {
  start_node: "prompt",
  nodes: [
    {
      id: "prompt",
      type: "message",
      text: "It's time to shave your head. Send a trimmer photo within 30 seconds.",
      next: "proof_window",
    },
    {
      id: "proof_window",
      type: "await",
      timeout_seconds: 30,
      on_timeout: "barrage",
      matches: [
        {
          condition: "The attached image visibly shows the requester's hair trimmer.",
          evidence: "image",
          next: "success",
        },
      ],
    },
    {
      id: "barrage",
      type: "repeat",
      interval_seconds: 5,
      max_occurrences: 20,
      messages: ["Trimmer picture. Now.", "The razor waits for no man."],
      matches: [
        {
          condition: "The attached image visibly shows the requester's hair trimmer.",
          evidence: "image",
          next: "success",
        },
      ],
      on_exhausted: "expired",
    },
    { id: "success", type: "complete", message: "Trimmer confirmed. Barrage stopped." },
    { id: "expired", type: "complete", message: "The barrage safety cap was reached." },
  ],
};

describe("durable event-driven workflows", () => {
  test("validates graph targets and paired await timeouts", () => {
    expect(
      workflowPlanSchema.safeParse({
        start_node: "start",
        nodes: [
          {
            id: "start",
            type: "await",
            timeout_seconds: 30,
            matches: [{ condition: "done", evidence: "text", next: "missing" }],
          },
          { id: "end", type: "complete" },
        ],
      }).success,
    ).toBeFalse();
    expect(workflowPlanSchema.safeParse(trimmerPlan).success).toBeTrue();
  });

  test("persists a graph and exposes only its current semantic matches", () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    const current = context({
      requesterId: "UOWNER",
      isOwner: true,
      surface: "dm",
      channelId: "D1",
    });
    const workflow = workflows.create({
      context: current,
      name: "Trimmer proof",
      plan: trimmerPlan,
      startsAt: 2_000,
      expiresAt: 100_000,
      delivery: "dm",
      now: 1_000,
    });
    expect(workflow.status).toBe("scheduled");
    expect(workflows.awaiting("T123", "UOWNER", 1_500)).toEqual([]);
    workflows.markActive(workflow.id, 2_000);
    workflows.enterNode(workflow.id, "prompt", "proof_window", 2_000);
    const candidates = ingressCandidates(workflows.awaiting("T123", "UOWNER", 2_001));
    expect(candidates).toEqual([
      {
        workflowId: workflow.id,
        name: "Trimmer proof",
        matches: [
          {
            index: 0,
            condition: "The attached image visibly shows the requester's hair trimmer.",
            evidence: "image",
          },
        ],
      },
    ]);
  });

  test("sleeps until a crashed worker lease can be recovered instead of busy-looping", () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    const workflow = workflows.create({
      context: context({ requesterId: "UOWNER", isOwner: true }),
      name: "Lease recovery",
      plan: trimmerPlan,
      startsAt: 2_000,
      expiresAt: 200_000,
      delivery: "dm",
      now: 1_000,
    });
    expect(workflows.leaseDue(2_000, 60_000).map((record) => record.id)).toEqual([workflow.id]);
    expect(workflows.nextDueAt(2_001)).toBe(62_000);
    expect(workflows.leaseDue(61_999)).toEqual([]);
    expect(workflows.leaseDue(62_000).map((record) => record.id)).toEqual([workflow.id]);
  });

  test("runs prompt, timeout, five-second barrage, and photo completion without a resident agent", async () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    const slack = fakeSlack();
    const engine = new WorkflowEngine(workflows, slack.client, async () => {});
    const workflow = workflows.create({
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" }),
      name: "Trimmer proof",
      plan: trimmerPlan,
      startsAt: 2_000,
      expiresAt: 200_000,
      delivery: "dm",
      cancelMessage: "Barrage called off.",
      now: 1_000,
    });

    await engine.handleDue(workflow.id, 2_000);
    expect(slack.posts.map((post) => post.text)).toEqual([
      "It's time to shave your head. Send a trimmer photo within 30 seconds.",
    ]);
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "active",
      current_node_id: "proof_window",
      next_run_at: 32_000,
    });

    await engine.handleDue(workflow.id, 32_000);
    expect(slack.posts.map((post) => post.text)).toEqual([
      "It's time to shave your head. Send a trimmer photo within 30 seconds.",
      "Trimmer picture. Now.",
    ]);
    expect(workflows.get(workflow.id)).toMatchObject({
      current_node_id: "barrage",
      next_run_at: 37_000,
      iteration: 1,
    });

    await engine.handleDue(workflow.id, 37_000);
    expect(slack.posts.at(-1)?.text).toBe("The razor waits for no man.");
    const outcome = await engine.applyIngress({
      matches: [{ workflowId: workflow.id, matchIndex: 0 }],
      cancelIds: [],
      workspaceId: "T123",
      creatorUserId: "UOWNER",
      now: 38_000,
    });
    expect(outcome).toEqual({ consumed: true, matched: 1, cancelled: 0 });
    expect(slack.posts.at(-1)?.text).toBe("Trimmer confirmed. Barrage stopped.");
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "completed",
      next_run_at: null,
    });
  });

  test("explicit call-off cancels a waiting workflow and posts its configured acknowledgment", async () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    const slack = fakeSlack();
    const engine = new WorkflowEngine(workflows, slack.client, async () => {});
    const workflow = workflows.create({
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" }),
      name: "Trimmer proof",
      plan: trimmerPlan,
      startsAt: 2_000,
      expiresAt: 200_000,
      delivery: "dm",
      cancelMessage: "Barrage called off.",
      now: 1_000,
    });
    await engine.handleDue(workflow.id, 2_000);
    const outcome = await engine.applyIngress({
      matches: [],
      cancelIds: [workflow.id],
      workspaceId: "T123",
      creatorUserId: "UOWNER",
      now: 3_000,
    });
    expect(outcome).toEqual({ consumed: true, matched: 0, cancelled: 1 });
    expect(slack.posts.at(-1)?.text).toBe("Barrage called off.");
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "cancelled",
      finished_reason: "explicit_cancel",
      deleted_at: 3_000,
    });
  });

  test("counts deduplicated trusted tool events across turns and soft-deletes on completion", async () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    const slack = fakeSlack();
    const engine = new WorkflowEngine(workflows, slack.client, async () => {});
    const current = context({ requesterId: "UOWNER", isOwner: true, surface: "dm" });
    const workflow = workflows.create({
      context: current,
      name: "Finish two tasks",
      plan: {
        start_node: "wait",
        nodes: [
          {
            id: "wait",
            type: "await",
            matches: [
              {
                condition: "Matt credibly says he completed two to-do-list items.",
                evidence: "text",
                next: "done",
              },
            ],
          },
          { id: "done", type: "complete", message: "Two tasks down. Workflow complete." },
        ],
      },
      completionPolicy: {
        kind: "trusted_event_count",
        event: "brain_list_item_removed",
        destination_title: "To Do",
        target: 2,
        completion_node: "done",
        summary: "Complete after two successful To Do removals.",
      },
      startsAt: 1_000,
      expiresAt: 100_000,
      delivery: "dm",
      now: 1_000,
    });
    await engine.handleDue(workflow.id, 1_000);

    expect(
      workflows.recordTrustedEvent({
        context: current,
        eventKind: "brain_list_item_removed",
        destinationTitle: "Shopping",
        eventKey: "wrong-list",
        now: 1_500,
      }),
    ).toEqual([]);
    expect(
      workflows.recordTrustedEvent({
        context: current,
        eventKind: "brain_list_item_removed",
        destinationTitle: "To Do",
        eventKey: "first-tool-call",
        now: 2_000,
      }),
    ).toEqual([
      {
        name: "Finish two tasks",
        summary: "Complete after two successful To Do removals.",
        current: 1,
        target: 2,
        completed: false,
      },
    ]);
    expect(
      workflows.recordTrustedEvent({
        context: current,
        eventKind: "brain_list_item_removed",
        destinationTitle: "To Do",
        eventKey: "first-tool-call",
        now: 2_001,
      }),
    ).toEqual([]);
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "active",
      current_node_id: "wait",
      state_json: JSON.stringify({ trustedEventCounts: { brain_list_item_removed: 1 } }),
    });

    expect(
      workflows.recordTrustedEvent({
        context: current,
        eventKind: "brain_list_item_removed",
        destinationTitle: "to do",
        eventKey: "second-tool-call",
        now: 3_000,
      }),
    ).toEqual([expect.objectContaining({ current: 2, target: 2, completed: true })]);
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "active",
      current_node_id: "done",
      next_run_at: 3_000,
    });

    await engine.handleDue(workflow.id, 3_000);
    expect(slack.posts.at(-1)?.text).toBe("Two tasks down. Workflow complete.");
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "completed",
      finished_reason: "completed",
      finished_at: 3_000,
      deleted_at: 3_000,
      next_run_at: null,
    });
    expect(workflows.list("T123", "UOWNER")).toEqual([]);
    expect(db.raw.query("SELECT count(*) AS count FROM workflows").get()).toEqual({ count: 1 });
    expect(db.raw.query("SELECT count(*) AS count FROM workflow_events").get()).toEqual({
      count: 2,
    });
  });

  test("rejects a trusted completion policy that does not target a complete node", () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    expect(() =>
      workflows.create({
        context: context({ requesterId: "UOWNER", isOwner: true }),
        name: "Invalid completion",
        plan: trimmerPlan,
        completionPolicy: {
          kind: "trusted_event_count",
          event: "brain_list_item_removed",
          destination_title: "To Do",
          target: 2,
          completion_node: "proof_window",
          summary: "Complete after two removals.",
        },
        startsAt: 2_000,
        expiresAt: 100_000,
        delivery: "dm",
        now: 1_000,
      }),
    ).toThrow("complete node");
  });

  test("never sends a repeat after the global message safety cap", async () => {
    const db = testDatabase();
    open.push(db);
    const workflows = new WorkflowRepository(db);
    const slack = fakeSlack();
    const engine = new WorkflowEngine(workflows, slack.client, async () => {});
    const workflow = workflows.create({
      context: context({ requesterId: "UOWNER", isOwner: true }),
      name: "Capped repeat",
      plan: {
        start_node: "nag",
        nodes: [
          {
            id: "nag",
            type: "repeat",
            messages: ["nag"],
            interval_seconds: 5,
            matches: [{ condition: "done", evidence: "text", next: "done" }],
          },
          { id: "done", type: "complete" },
        ],
      },
      startsAt: 2_000,
      expiresAt: 200_000,
      delivery: "dm",
      now: 1_000,
    });
    db.raw.query("UPDATE workflows SET message_count = 500 WHERE id = ?").run(workflow.id);
    await engine.handleDue(workflow.id, 2_000);
    expect(slack.posts).toEqual([]);
    expect(workflows.get(workflow.id)).toMatchObject({
      status: "failed",
      last_error: "Workflow reached its message safety cap.",
      finished_reason: "failed",
      deleted_at: 2_000,
    });
  });
});
