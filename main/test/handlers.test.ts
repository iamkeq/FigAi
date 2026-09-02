import { afterEach, describe, expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type Agent, type AgentResult, AgentRunError } from "../src/agent/openrouter.ts";
import { parseConfig } from "../src/config.ts";
import { MattDatabase } from "../src/db/database.ts";
import { type DirectivePolicy, TemporaryDirectiveRepository } from "../src/db/directives.ts";
import { MemoryRepository } from "../src/db/memories.ts";
import { SkillRepository } from "../src/db/skills.ts";
import { WorkflowRepository } from "../src/db/workflows.ts";
import { AttachmentManager } from "../src/files.ts";
import { SlackAuthorizer } from "../src/slack/authorization.ts";
import type { SlackClient } from "../src/slack/client.ts";
import type { SlashCommands } from "../src/slack/commands.ts";
import { type SlackEventInput, SlackHandlers } from "../src/slack/handlers.ts";
import type { ThreadMessage } from "../src/types.ts";
import { WorkflowEngine } from "../src/workflows/engine.ts";
import { context } from "./helpers.ts";

const databases: MattDatabase[] = [];
afterEach(() => {
  while (databases.length) databases.pop()?.close();
});

const config = parseConfig({
  SLACK_BOT_TOKEN: "xoxb-test",
  SLACK_APP_TOKEN: "xapp-test",
  OPENROUTER_API_KEY: "sk-or-v1-test",
  OWNER_USER_ID: "UOWNER",
  ALLOWED_CHANNEL_IDS: "C123",
  FIGAI_DATA_DIR: "/tmp/figai-handler-test",
});

function success(text = "done"): AgentResult {
  return {
    text,
    model: "test/model",
    latencyMs: 1,
    promptTokens: 1,
    completionTokens: 1,
    totalTokens: 2,
    reportedCost: 0,
    tools: [],
    writeReceipts: [],
    images: [],
  };
}

function event(overrides: Partial<SlackEventInput> = {}): SlackEventInput {
  return {
    eventId: "Ev1",
    workspaceId: "T123",
    kind: "app_mention",
    channelId: "C123",
    userId: "U123",
    ts: "100.001",
    text: "<@UBOT> hello",
    ...overrides,
  };
}

function fakeSlack(
  input: { messages?: ThreadMessage[]; external?: boolean; members?: string[] } = {},
): {
  client: SlackClient;
  posts: Array<Parameters<SlackClient["chat"]["postMessage"]>[0]>;
  statuses: Array<Parameters<SlackClient["assistant"]["threads"]["setStatus"]>[0]>;
  uploads: Array<Parameters<SlackClient["filesUploadV2"]>[0]>;
} {
  const posts: Array<Parameters<SlackClient["chat"]["postMessage"]>[0]> = [];
  const statuses: Array<Parameters<SlackClient["assistant"]["threads"]["setStatus"]>[0]> = [];
  const uploads: Array<Parameters<SlackClient["filesUploadV2"]>[0]> = [];
  return {
    posts,
    statuses,
    uploads,
    client: {
      filesUploadV2: async (args) => {
        uploads.push(args);
        return { ok: true };
      },
      auth: { test: async () => ({ ok: true }) },
      users: {
        info: async ({ user }) => ({
          ok: true,
          user: { id: user, team_id: "T123", tz: "America/New_York", is_stranger: input.external },
        }),
      },
      conversations: {
        info: async () => ({ ok: true }),
        members: async () => ({
          ok: true,
          members: input.members ?? ["U123"],
          response_metadata: { next_cursor: "" },
        }),
        replies: async () => ({ ok: true, messages: input.messages ?? [] }),
      },
      chat: {
        postMessage: async (args) => {
          posts.push(args);
          return { ok: true };
        },
      },
      assistant: {
        threads: {
          setStatus: async (args) => {
            statuses.push(args);
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

function handlers(input: {
  slack: SlackClient;
  agent: Pick<Agent, "run"> &
    Partial<
      Pick<
        Agent,
        | "loadingStatus"
        | "evaluateDirectiveIngress"
        | "evaluateWorkflowIngress"
        | "compileDirectivePolicy"
      >
    >;
  attachments?: AttachmentManager;
  enableWorkflows?: boolean;
}): { value: SlackHandlers; db: MattDatabase; workflows: WorkflowRepository | null } {
  const db = new MattDatabase(":memory:");
  db.migrate();
  databases.push(db);
  const authorizer = new SlackAuthorizer(input.slack, config, "T123");
  const workflows = input.enableWorkflows ? new WorkflowRepository(db) : null;
  const workflowEngine = workflows
    ? new WorkflowEngine(workflows, input.slack, async () => {})
    : null;
  const value = new SlackHandlers(
    config,
    db,
    input.slack,
    authorizer,
    new MemoryRepository(db),
    new SkillRepository(db),
    input.attachments ?? new AttachmentManager("xoxb-test"),
    {
      loadingStatus: async () => [],
      evaluateDirectiveIngress: async () => ({
        satisfiedIds: [],
        bypassIds: [],
        outcome: "evaluated",
        reasonCode: "test",
      }),
      compileDirectivePolicy: async () => ({
        version: 1,
        kind: "custom",
        delivery: "normal",
        tools: "semantic",
        requirements: ["Follow the directive."],
        summary: "Follow the temporary directive",
      }),
      evaluateWorkflowIngress: async () => ({
        matches: [],
        cancelIds: [],
        outcome: "evaluated",
        reasonCode: "test",
      }),
      ...input.agent,
    } as Agent,
    { execute: () => "unused" } as unknown as SlashCommands,
    "UBOT",
    undefined,
    undefined,
    undefined,
    workflows,
    workflowEngine,
  );
  return { value, db, workflows };
}

describe("Socket Mode event handling", () => {
  test("an approved mention creates exactly one threaded reply and suppresses duplicate event IDs", async () => {
    const slack = fakeSlack({ messages: [{ ts: "100.001", user: "U123", text: "<@UBOT> hello" }] });
    let turns = 0;
    const setup = handlers({
      slack: slack.client,
      agent: {
        loadingStatus: async () => [
          "is reading the greeting",
          "is checking the thread",
          "is thinking through the reply",
          "is writing the answer",
          "is finishing the response",
        ],
        run: async () => {
          turns += 1;
          return success();
        },
      },
    });
    await setup.value.handleEvent(event());
    await setup.value.handleEvent(event());
    expect(turns).toBe(1);
    expect(slack.posts).toEqual([
      {
        channel: "C123",
        thread_ts: "100.001",
        text: "done",
        blocks: [{ type: "markdown", text: "done" }],
        unfurl_links: false,
        unfurl_media: false,
      },
    ]);
    expect(slack.statuses[0]).toMatchObject({
      channel_id: "C123",
      thread_ts: "100.001",
      status: "is reading",
      loading_messages: ["is checking", "is thinking", "is writing", "is finishing up"],
    });
    expect(slack.statuses[1]).toMatchObject({
      status: "is reading the greeting",
      loading_messages: [
        "is checking the thread",
        "is thinking through the reply",
        "is writing the answer",
        "is finishing the response",
      ],
    });
    expect(slack.statuses.at(-1)?.status).toBe("");
    expect(setup.db.raw.query("SELECT status FROM interactions").all()).toEqual([{ status: "ok" }]);
  });

  test("records an explicitly silent normal turn without posting an error or reply", async () => {
    const slack = fakeSlack({ messages: [{ ts: "100.001", user: "U123", text: "hello?" }] });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => ({ ...success(""), suppressDelivery: true }),
      },
    });
    await setup.value.handleEvent(event({ text: "hello?" }));
    expect(slack.posts).toEqual([]);
    expect(slack.uploads).toEqual([]);
    expect(slack.statuses.at(-1)?.status).toBe("");
    expect(setup.db.raw.query("SELECT status FROM interactions").get()).toEqual({ status: "ok" });
  });

  test("consumes matching photographic workflow evidence and skips the ordinary agent turn", async () => {
    const slack = fakeSlack();
    let normalTurns = 0;
    const setup = handlers({
      slack: slack.client,
      enableWorkflows: true,
      attachments: new AttachmentManager(
        "xoxb-test",
        async () => new Response(readFileSync(join(import.meta.dir, "fixtures/sample.gif"))),
      ),
      agent: {
        evaluateWorkflowIngress: async (input) => {
          expect(input.attachmentParts.some((part) => part.type === "image_url")).toBeTrue();
          return {
            matches: [{ workflowId: input.candidates[0]?.workflowId ?? -1, matchIndex: 0 }],
            cancelIds: [],
            outcome: "evaluated",
            reasonCode: "test_match",
          };
        },
        run: async () => {
          normalTurns += 1;
          return success();
        },
      },
    });
    const workflows = setup.workflows;
    if (!workflows) throw new Error("test workflow repository missing");
    const now = Date.now();
    const workflow = workflows.create({
      context: context({ requesterId: "U123", surface: "channel", channelId: "C123" }),
      name: "Proof check",
      plan: {
        start_node: "wait",
        nodes: [
          {
            id: "wait",
            type: "await",
            matches: [{ condition: "Image visibly shows proof.", evidence: "image", next: "done" }],
          },
          { id: "done", type: "complete", message: "Proof accepted." },
        ],
      },
      startsAt: now,
      expiresAt: now + 60_000,
      delivery: "thread",
      now,
    });
    const file = {
      id: "F1",
      name: "proof.gif",
      mimetype: "image/gif",
      size: readFileSync(join(import.meta.dir, "fixtures/sample.gif")).byteLength,
      url_private_download: "https://files.example/proof.gif",
    };
    await setup.value.handleEvent(
      event({
        eventId: "EvProof",
        kind: "message_channel",
        channelType: "channel",
        ts: String((now + 1) / 1_000),
        text: "Here is proof.",
        files: [file],
        subtype: "file_share",
      }),
    );
    expect(normalTurns).toBe(0);
    expect(workflows.get(workflow.id)?.status).toBe("completed");
    expect(slack.posts.map((post) => post.text)).toEqual(["<@U123> Proof accepted."]);
    expect(setup.db.raw.query("SELECT tools_json FROM interactions").get()).toEqual({
      tools_json: '["workflow_ingress"]',
    });
  });

  test("does not leak an error reply while compiled delivery suppression is active", async () => {
    const now = Date.now();
    const slackTs = String(now / 1_000);
    const slack = fakeSlack({ messages: [{ ts: slackTs, user: "U123", text: "hello?" }] });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => {
          throw new Error("test provider failure");
        },
      },
    });
    new TemporaryDirectiveRepository(setup.db).create({
      context: context({ workspaceId: "T123", requesterId: "U123" }),
      text: "Do not respond until I finish the task.",
      policy: {
        version: 1,
        kind: "delivery_suppression",
        delivery: "suppress",
        tools: "block_all",
        requirements: ["Do not send a Slack response while active."],
        summary: "Remain silent",
      },
      releasePhrase: "I finished the task",
      now: now - 1_000,
    });

    await setup.value.handleEvent(
      event({
        eventId: "EvSuppressedFailure",
        kind: "message_im",
        channelId: "D1",
        channelType: "im",
        ts: slackTs,
        text: "hello?",
      }),
    );

    expect(slack.posts).toEqual([]);
    expect(setup.db.raw.query("SELECT status FROM interactions").get()).toEqual({
      status: "error",
    });
  });

  test("uses semantic judgment to release any conditional directive before normal work", async () => {
    const slack = fakeSlack({
      messages: [{ ts: "2", user: "U123", text: "I finished the report" }],
    });
    let turns = 0;
    let releasedPrompt = "";
    const setup = handlers({
      slack: slack.client,
      agent: {
        evaluateDirectiveIngress: async (directives, message) => ({
          satisfiedIds:
            message === "The report has been submitted!" ? directives.map(({ id }) => id) : [],
          bypassIds: [],
          outcome: "evaluated",
          reasonCode: "test",
        }),
        run: async ({ directives, messages }) => {
          turns += 1;
          if (!directives?.length) releasedPrompt = String(messages[0]?.content ?? "");
          return directives?.length
            ? { ...success(""), suppressDelivery: true }
            : success("Welcome back.");
        },
      },
    });
    const directive = new TemporaryDirectiveRepository(setup.db).create({
      context: context({ workspaceId: "T123", requesterId: "U123" }),
      text: "Do not respond until the report is finished.",
      releasePhrase: "I finished the report",
      now: 1_000,
    });

    await setup.value.handleEvent(
      event({
        eventId: "EvLocked",
        kind: "message_im",
        channelId: "D1",
        channelType: "im",
        ts: "1",
        text: "help me procrastinate",
      }),
    );
    expect(turns).toBe(1);
    expect(slack.posts).toEqual([]);
    expect(setup.db.raw.query("SELECT status FROM interactions").get()).toEqual({
      status: "ok",
    });

    await setup.value.handleEvent(
      event({
        eventId: "EvReleased",
        kind: "message_im",
        channelId: "D1",
        channelType: "im",
        ts: "2",
        text: "The report has been submitted!",
      }),
    );
    expect(turns).toBe(2);
    expect(slack.posts.at(-1)?.text).toBe("Welcome back.");
    expect(new TemporaryDirectiveRepository(setup.db).get(directive.id)?.resolution).toBe(
      "completed",
    );
    expect(releasedPrompt).toContain("Just-satisfied temporary directives");
    expect(releasedPrompt).toContain("Do not respond until the report is finished.");
    expect(releasedPrompt).toContain("I finished the report");
  });

  test("does not apply or evaluate a directive before its stored start time", async () => {
    const slack = fakeSlack({ messages: [{ ts: "1", user: "U123", text: "hello" }] });
    let releaseChecks = 0;
    let receivedDirectives = -1;
    const setup = handlers({
      slack: slack.client,
      agent: {
        evaluateDirectiveIngress: async () => {
          releaseChecks += 1;
          return {
            satisfiedIds: [],
            bypassIds: [],
            outcome: "evaluated",
            reasonCode: "test",
          };
        },
        run: async ({ directives }) => {
          receivedDirectives = directives?.length ?? 0;
          return success("Normal reply.");
        },
      },
    });
    new TemporaryDirectiveRepository(setup.db).create({
      context: context({ workspaceId: "T123", requesterId: "U123" }),
      text: "Do not respond until the report is finished.",
      releasePhrase: "I finished the report",
      startsAt: Date.now() + 60_000,
    });

    await setup.value.handleEvent(event({ eventId: "EvBeforeStart", text: "hello" }));

    expect(releaseChecks).toBe(0);
    expect(receivedDirectives).toBe(0);
    expect(slack.posts.at(-1)?.text).toBe("Normal reply.");
  });

  test("lets an explicit management request bypass compiled silence for one turn", async () => {
    const now = Date.now();
    const slackTs = String(now / 1_000);
    const slack = fakeSlack({
      messages: [{ ts: slackTs, user: "U123", text: "cancel that rule" }],
    });
    let receivedDirectives = -1;
    const setup = handlers({
      slack: slack.client,
      agent: {
        evaluateDirectiveIngress: async (directives) => ({
          satisfiedIds: [],
          bypassIds: directives.map(({ id }) => id),
          outcome: "evaluated",
          reasonCode: "test_management_request",
        }),
        run: async ({ directives }) => {
          receivedDirectives = directives?.length ?? 0;
          return success("I can manage that directive on this turn.");
        },
      },
    });
    const policy: DirectivePolicy = {
      version: 1,
      kind: "delivery_suppression",
      delivery: "suppress",
      tools: "block_all",
      requirements: ["Do not send a Slack response while active."],
      summary: "Remain silent",
    };
    const directive = new TemporaryDirectiveRepository(setup.db).create({
      context: context({ workspaceId: "T123", requesterId: "U123" }),
      text: "Ignore me for one minute.",
      policy,
      now: now - 1_000,
      expiresAt: now + 60_000,
    });

    await setup.value.handleEvent(
      event({
        eventId: "EvManageDirective",
        kind: "message_im",
        channelId: "D1",
        channelType: "im",
        ts: slackTs,
        text: "cancel that rule",
      }),
    );

    expect(receivedDirectives).toBe(0);
    expect(slack.posts.at(-1)?.text).toBe("I can manage that directive on this turn.");
    expect(new TemporaryDirectiveRepository(setup.db).get(directive.id)?.resolution).toBeNull();
  });

  test("authorized one-to-one DMs work and non-members are denied without model use", async () => {
    const allowedSlack = fakeSlack({ messages: [{ ts: "1", user: "U123", text: "hello" }] });
    let turns = 0;
    const allowed = handlers({
      slack: allowedSlack.client,
      agent: {
        run: async () => {
          turns += 1;
          return success("dm reply");
        },
      },
    });
    await allowed.value.handleEvent(
      event({
        eventId: "Dm1",
        kind: "message_im",
        channelId: "D123",
        channelType: "im",
        ts: "1",
        text: "hello",
      }),
    );
    expect(turns).toBe(1);
    expect(allowedSlack.posts[0]?.text).toBe("dm reply");

    const deniedSlack = fakeSlack({ members: [] });
    const denied = handlers({
      slack: deniedSlack.client,
      agent: {
        run: async () => {
          turns += 1;
          return success();
        },
      },
    });
    await denied.value.handleEvent(
      event({
        eventId: "Dm2",
        kind: "message_im",
        channelId: "D999",
        channelType: "im",
        ts: "2",
        text: "hello",
      }),
    );
    expect(turns).toBe(1);
    expect(deniedSlack.posts[0]?.text).toContain("limited to internal members");
  });

  test("posts an agent progress update before the final response", async () => {
    const slack = fakeSlack({ messages: [{ ts: "100.001", user: "U123", text: "research it" }] });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async ({ onProgress }) => {
          await onProgress?.("I’m checking the long version now.");
          return success("Here’s the final result.");
        },
      },
    });
    await setup.value.handleEvent(event({ text: "research it" }));
    expect(slack.posts.map((post) => post.text)).toEqual([
      "I’m checking the long version now.",
      "Here’s the final result.",
    ]);
    expect(slack.statuses[1]).toMatchObject({
      status: "is reading",
      loading_messages: ["is checking", "is thinking", "is writing", "is finishing up"],
    });
    expect(slack.statuses.at(-1)?.status).toBe("");
  });

  test("adds a subtle receipt footer only for successful writes", async () => {
    const slack = fakeSlack({ messages: [{ ts: "100.001", user: "U123", text: "save it" }] });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => ({
          ...success("Saved."),
          tools: ["brain_save"],
          writeReceipts: ["Saved to Matt-Private", "Saved to Matt-Private"],
        }),
      },
    });
    await setup.value.handleEvent(event({ text: "save it" }));
    expect(slack.posts[0]?.text).toBe("Saved.\n\n*✓ Saved to Matt-Private*");
  });

  test("preserves successful write receipts when final response generation fails", async () => {
    const slack = fakeSlack({
      messages: [{ ts: "100.001", user: "U123", text: "make a reminder" }],
    });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => {
          throw new AgentRunError(new Error("provider failed"), ["Reminder created"]);
        },
      },
    });
    await setup.value.handleEvent(event({ text: "make a reminder" }));
    expect(slack.posts[0]?.text).toBe(
      "I hit an internal error after completing the actions below.\n\n*✓ Reminder created*",
    );
  });

  test("uploads generated images into the invoking Slack thread", async () => {
    const slack = fakeSlack({ messages: [{ ts: "100.001", user: "U123", text: "make art" }] });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => ({
          ...success("Here it is."),
          images: [
            { bytes: Buffer.from([1, 2, 3]), mediaType: "image/png", altText: "Orange robot" },
          ],
        }),
      },
    });
    await setup.value.handleEvent(event({ text: "make art" }));
    expect(slack.uploads).toHaveLength(1);
    expect(slack.uploads[0]).toMatchObject({
      channel_id: "C123",
      thread_ts: "100.001",
      filename: "figai-image-1.png",
      title: "FigAi generated image",
      alt_text: "Orange robot",
    });
    expect(slack.posts[0]?.text).toBe("Here it is.");
  });

  test("uploads a Brain map with its safe filename and title", async () => {
    const slack = fakeSlack({ messages: [{ ts: "100.001", user: "U123", text: "map it" }] });
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => ({
          ...success("Attached your Brain map."),
          images: [
            {
              bytes: Buffer.from([1, 2, 3]),
              mediaType: "image/png",
              altText: "Brain map with 4 notes",
              filename: "brain-map.png",
              title: "Brain map",
            },
          ],
        }),
      },
    });

    await setup.value.handleEvent(event({ text: "map it" }));

    expect(slack.uploads[0]).toMatchObject({
      channel_id: "C123",
      thread_ts: "100.001",
      filename: "brain-map.png",
      title: "Brain map",
      alt_text: "Brain map with 4 notes",
    });
  });

  test("gives generated loading statuses recent context for ambiguous follow-ups", async () => {
    const slack = fakeSlack({
      messages: [
        { ts: "100.001", user: "U123", text: "Compare these deployment options" },
        { ts: "100.002", user: "UBOT", text: "Option B is safer but slower." },
        { ts: "100.003", user: "U123", text: "Yes, do that" },
      ],
    });
    let loadingInput = "";
    const setup = handlers({
      slack: slack.client,
      agent: {
        loadingStatus: async (input) => {
          loadingInput = input;
          return [];
        },
        run: async () => success(),
      },
    });
    await setup.value.handleEvent(event({ ts: "100.003", text: "Yes, do that" }));
    expect(loadingInput).toContain("PRIOR CONTEXT");
    expect(loadingInput).toContain("User: Compare these deployment options");
    expect(loadingInput).toContain("Assistant: Option B is safer but slower.");
    expect(loadingInput).toContain("CURRENT MESSAGE");
    expect(loadingInput).toContain("Yes, do that");
    expect(slack.statuses[0]).toMatchObject({
      status: "is reading",
      loading_messages: ["is checking", "is thinking", "is writing", "is finishing up"],
    });
  });

  test("scopes profile tools to current-thread participants without persisting profile data", async () => {
    const slack = fakeSlack({
      messages: [
        { ts: "100.001", user: "U123", text: "Look at their avatar" },
        { ts: "100.002", user: "UPARTICIPANT", text: "hello" },
        { ts: "100.003", user: "UBOT", text: "prior reply" },
      ],
    });
    let participants: ReadonlySet<string> | undefined;
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async ({ context }) => {
          participants = context.participantIds;
          return { ...success(), tools: ["get_user_profile"] };
        },
      },
    });
    await setup.value.handleEvent(event({ text: "Look at their avatar" }));
    expect([...(participants ?? [])]).toEqual(["U123", "UPARTICIPANT"]);
    const stored = setup.db.raw
      .query("SELECT tools_json FROM interactions WHERE status = 'ok'")
      .get() as { tools_json: string };
    expect(stored.tools_json).toBe('["get_user_profile"]');
    expect(JSON.stringify(stored)).not.toContain("avatar");
  });

  test("adds enabled skill metadata to turns without injecting instruction bodies", async () => {
    const slack = fakeSlack({
      messages: [{ ts: "100.001", user: "U123", text: "<@UBOT> write release notes" }],
    });
    let systemPrompt = "";
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async ({ messages }) => {
          systemPrompt = String(messages[0]?.content ?? "");
          return success();
        },
      },
    });
    setup.db.raw
      .query(`
        INSERT INTO skills(
          name, description, instructions, creator_user_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
      `)
      .run(
        "Release notes",
        "Formats concise customer-facing release notes",
        "SECRET FULL INSTRUCTION BODY",
        "UOWNER",
        1,
        1,
      );
    await setup.value.handleEvent(event({ text: "<@UBOT> write release notes" }));
    expect(systemPrompt).toContain('"name":"Brain Librarian"');
    expect(systemPrompt).toContain('"name":"Release notes"');
    expect(systemPrompt).not.toContain("SECRET FULL INSTRUCTION BODY");
    expect(systemPrompt).not.toContain("Make the filing decision");
  });

  test("ignores bots, edits, group DMs, wrong workspaces, and unapproved channels", async () => {
    const slack = fakeSlack();
    let turns = 0;
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => {
          turns += 1;
          return success();
        },
      },
    });
    await setup.value.handleEvent(event({ eventId: "1", botId: "B1" }));
    await setup.value.handleEvent(event({ eventId: "2", subtype: "message_changed" }));
    await setup.value.handleEvent(event({ eventId: "3", kind: "message_im", channelType: "mpim" }));
    await setup.value.handleEvent(event({ eventId: "4", workspaceId: "TOTHER" }));
    await setup.value.handleEvent(event({ eventId: "5", channelId: "COTHER" }));
    await setup.value.handleEvent(
      event({
        eventId: "6",
        kind: "message_channel",
        channelType: "channel",
        text: "ordinary unmentioned channel chatter",
      }),
    );
    expect(turns).toBe(0);
    expect(slack.posts).toHaveLength(0);
    expect(setup.db.raw.query("SELECT count(*) AS count FROM processed_events").get()).toEqual({
      count: 0,
    });
  });

  test("serializes turns in the same Slack thread", async () => {
    const slack = fakeSlack();
    let active = 0;
    let peak = 0;
    const setup = handlers({
      slack: slack.client,
      agent: {
        run: async () => {
          active += 1;
          peak = Math.max(peak, active);
          await Bun.sleep(10);
          active -= 1;
          return success();
        },
      },
    });
    await Promise.all([
      setup.value.handleEvent(event({ eventId: "A", ts: "100.002", threadTs: "100.001" })),
      setup.value.handleEvent(event({ eventId: "B", ts: "100.003", threadTs: "100.001" })),
    ]);
    expect(peak).toBe(1);
  });

  test("removes temporary files after provider failure", async () => {
    const bytes = readFileSync(join(import.meta.dir, "fixtures", "sample.gif"));
    const slack = fakeSlack({
      messages: [
        {
          ts: "100.001",
          user: "U123",
          text: "inspect this",
          files: [
            {
              id: "F1",
              name: "sample.gif",
              mimetype: "image/gif",
              size: bytes.length,
              url_private_download: "https://slack.test/file",
            },
          ],
        },
      ],
    });
    const attachmentManager = new AttachmentManager("xoxb-test", async () => new Response(bytes));
    const before = new Set(readdirSync(tmpdir()).filter((name) => name.startsWith("figai-")));
    const setup = handlers({
      slack: slack.client,
      attachments: attachmentManager,
      agent: { run: async () => Promise.reject(new Error("provider failed")) },
    });
    await setup.value.handleEvent(event({ text: "inspect this" }));
    expect(
      [...readdirSync(tmpdir())].filter((name) => name.startsWith("figai-") && !before.has(name)),
    ).toHaveLength(0);
    expect(slack.posts.at(-1)?.text).toContain("internal error");
    expect(slack.statuses.at(-1)?.status).toBe("");
  });
});
