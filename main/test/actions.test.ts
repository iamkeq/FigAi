import { afterEach, describe, expect, test } from "bun:test";
import { ActionJournalRepository } from "../src/db/actions.ts";
import { context, testDatabase } from "./helpers.ts";

const open: ReturnType<typeof testDatabase>[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

describe("scoped action journal", () => {
  test("retains only allowlisted metadata and never raw tool data", () => {
    const db = testDatabase();
    open.push(db);
    const actions = new ActionJournalRepository(db);
    const current = context({ turnId: "Ev-sensitive" });
    const secretCommand = "SECRET_COMMAND_READ_THE_PAYROLL";
    const avatarUrl = "https://avatars.slack-edge.example/private-avatar.png";
    const targetUserId = "UCONFIDENTIAL";

    actions.recordToolCall({
      toolName: "create_scheduled_task",
      toolResult: {
        ok: true,
        result: { id: 42, next_run_at: Date.UTC(2099, 0, 1), text: secretCommand },
      },
      context: current,
      now: 100,
    });
    actions.recordToolCall({
      toolName: "get_user_profile",
      toolResult: {
        ok: true,
        result: {
          profile: { id: targetUserId, displayName: "Sensitive Name", image512: avatarUrl },
          avatar: Buffer.from("avatar bytes").toString("base64"),
        },
      },
      context: current,
      now: 101,
    });
    actions.recordToolCall({
      toolName: "brain_remove_list_item",
      toolResult: {
        ok: true,
        result: { removed: true, text: "SECRET APPLECARE TASK", path: "secret/to-do.md" },
      },
      context: current,
      now: 102,
    });
    actions.recordToolCall({
      toolName: "create_workflow",
      toolResult: {
        ok: true,
        result: {
          id: 77,
          name: "SECRET_TRIMMER_BARRAGE",
          plan: { messages: ["SECRET_NAG_TEXT"] },
          nextRunAt: Date.UTC(2099, 0, 2),
        },
      },
      context: current,
      now: 103,
    });
    actions.recordToolCall({
      toolName: "model_supplied\nsecret_tool_name",
      toolResult: { ok: false, error: "xoxb-super-secret" },
      context: current,
      now: 104,
    });

    const stored = JSON.stringify(db.raw.query("SELECT * FROM action_journal ORDER BY id").all());
    expect(stored).not.toContain(secretCommand);
    expect(stored).not.toContain(avatarUrl);
    expect(stored).not.toContain(targetUserId);
    expect(stored).not.toContain("avatar bytes");
    expect(stored).not.toContain("xoxb-super-secret");
    expect(stored).not.toContain("SECRET APPLECARE TASK");
    expect(stored).not.toContain("secret/to-do.md");
    expect(stored).not.toContain("SECRET_TRIMMER_BARRAGE");
    expect(stored).not.toContain("SECRET_NAG_TEXT");
    expect(stored).not.toContain("model_supplied");
    expect(stored).toContain("unknown_tool");

    const listed = actions.list({ context: current, limit: 20 });
    expect(JSON.stringify(listed)).not.toContain("42");
    expect(JSON.stringify(listed)).not.toContain(current.turnId);
    expect(JSON.stringify(listed)).not.toContain(current.requesterId);
    expect(listed).toMatchObject({
      trusted: true,
      scope: "current_slack_thread_and_requester",
      actions: [
        { tool: "unknown_tool", outcome: "failed", summary: "Tool call failed: unknown_tool" },
        {
          tool: "create_workflow",
          outcome: "succeeded",
          summary: "Created a durable event-driven workflow",
          scheduledFor: "2099-01-02T00:00:00.000Z",
        },
        {
          tool: "brain_remove_list_item",
          outcome: "succeeded",
          summary: "Removed an explicitly requested Brain list item",
        },
        {
          tool: "get_user_profile",
          outcome: "succeeded",
          summary: "Retrieved an authorized Slack profile",
        },
        {
          tool: "create_scheduled_task",
          outcome: "succeeded",
          summary: "Created a scheduled task",
          scheduledFor: "2099-01-01T00:00:00.000Z",
        },
      ],
    });
  });

  test("requires an exact workspace, channel, thread, and requester match", () => {
    const db = testDatabase();
    open.push(db);
    const actions = new ActionJournalRepository(db);
    const current = context();
    const variants = [
      current,
      context({ workspaceId: "TOTHER" }),
      context({ channelId: "COTHER" }),
      context({ threadTs: "200.000" }),
      context({ requesterId: "UOTHER" }),
    ];
    for (const [sequence, scopedContext] of variants.entries()) {
      actions.recordToolCall({
        toolName: sequence === 0 ? "brain_search" : "brain_list",
        toolResult: { ok: true, result: {} },
        context: scopedContext,
        now: 100 + sequence,
      });
    }

    expect(actions.list({ context: current, limit: 100 })).toMatchObject({
      actions: [{ tool: "brain_search" }],
    });
  });

  test("recognizes the compiled URL and media tools without retaining results", () => {
    const db = testDatabase();
    open.push(db);
    const actions = new ActionJournalRepository(db);
    const current = context();
    actions.recordToolCall({
      toolName: "fetch_url",
      toolResult: {
        ok: true,
        result: { sourceUrl: "https://private.example/path", text: "sensitive page text" },
      },
      context: current,
      now: 100,
    });
    actions.recordToolCall({
      toolName: "inspect_media_service",
      toolResult: { ok: false, error: "secret service transport detail" },
      context: current,
      now: 101,
    });
    actions.recordToolCall({
      toolName: "add_media",
      toolResult: {
        ok: true,
        result: { added: true, title: "private requested movie", tmdbId: 12345 },
      },
      context: current,
      now: 102,
    });
    actions.recordToolCall({
      toolName: "manage_sonarr_episodes",
      toolResult: {
        ok: true,
        result: {
          action: "delete_episode_files",
          performed: true,
          selectedEpisodes: [{ title: "private episode title", episodeFileId: 8675309 }],
        },
      },
      context: current,
      now: 103,
    });

    const listed = actions.list({ context: current, limit: 10 });
    expect(listed).toMatchObject({
      actions: [
        {
          tool: "manage_sonarr_episodes",
          outcome: "succeeded",
          summary: "Deleted selected Sonarr episode files",
        },
        {
          tool: "add_media",
          outcome: "succeeded",
          summary: "Added requested media to a configured local service",
        },
        {
          tool: "inspect_media_service",
          outcome: "failed",
          summary: "Tool call failed: inspect_media_service",
        },
        {
          tool: "fetch_url",
          outcome: "succeeded",
          summary: "Fetched an explicitly supplied public URL",
        },
      ],
    });
    const serialized = JSON.stringify(db.raw.query("SELECT * FROM action_journal").all());
    expect(serialized).not.toContain("private.example");
    expect(serialized).not.toContain("sensitive page text");
    expect(serialized).not.toContain("secret service transport detail");
    expect(serialized).not.toContain("private requested movie");
    expect(serialized).not.toContain("12345");
    expect(serialized).not.toContain("private episode title");
    expect(serialized).not.toContain("8675309");
  });

  test("records successful silent scheduled completion without retaining its condition", () => {
    const db = testDatabase();
    open.push(db);
    const actions = new ActionJournalRepository(db);
    const current = context({ turnId: "scheduled-task:42:100" });
    actions.recordToolCall({
      toolName: "complete_scheduled_task_silently",
      toolResult: { ok: true, result: { deliverySuppressed: true, secretCondition: "Ox Alpha" } },
      context: current,
      now: 100,
    });
    expect(actions.list({ context: current, limit: 10 })).toMatchObject({
      actions: [
        {
          tool: "complete_scheduled_task_silently",
          outcome: "succeeded",
          summary: "Completed a scheduled task without a message",
        },
      ],
    });
    expect(JSON.stringify(db.raw.query("SELECT * FROM action_journal").all())).not.toContain(
      "Ox Alpha",
    );
  });

  test("journals directive corrections without retaining directive or response text", () => {
    const db = testDatabase();
    open.push(db);
    const actions = new ActionJournalRepository(db);
    const current = context();
    actions.recordToolCall({
      toolName: "directive_compliance",
      toolResult: {
        ok: true,
        result: { action: "retry", directive: "SECRET DIRECTIVE", draft: "SECRET DRAFT" },
      },
      context: current,
      now: 100,
    });
    actions.recordToolCall({
      toolName: "directive_compliance",
      toolResult: { ok: true, result: { action: "suppress" } },
      context: current,
      now: 101,
    });

    expect(actions.list({ context: current, limit: 10 })).toMatchObject({
      actions: [
        {
          tool: "directive_compliance",
          outcome: "succeeded",
          summary: "Suppressed a response under an active directive",
        },
        {
          tool: "directive_compliance",
          outcome: "succeeded",
          summary: "Regenerated a response under an active directive",
        },
      ],
    });
    const stored = JSON.stringify(db.raw.query("SELECT * FROM action_journal").all());
    expect(stored).not.toContain("SECRET DIRECTIVE");
    expect(stored).not.toContain("SECRET DRAFT");
  });

  test("records no-change cancellation outcomes without leaking IDs", () => {
    const db = testDatabase();
    open.push(db);
    const actions = new ActionJournalRepository(db);
    const current = context();
    actions.recordToolCall({
      toolName: "cancel_reminder",
      toolResult: { ok: true, result: { id: 987654, cancelled: false } },
      context: current,
      now: 100,
    });
    const listed = actions.list({ context: current, limit: 10 });
    expect(listed).toMatchObject({
      actions: [
        {
          tool: "cancel_reminder",
          outcome: "no_change",
          summary: "A schedule cancellation request made no change",
        },
      ],
    });
    expect(JSON.stringify(listed)).not.toContain("987654");
  });
});
