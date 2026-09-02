import { afterEach, describe, expect, test } from "bun:test";
import { ToolExecutor, toolDefinitions } from "../src/agent/tools.ts";
import type { BrainRepository } from "../src/brain/vault.ts";
import { ActionJournalRepository } from "../src/db/actions.ts";
import { BackupManager } from "../src/db/backup.ts";
import type { MattDatabase } from "../src/db/database.ts";
import type { DirectivePolicyCompiler } from "../src/db/directives.ts";
import { MemoryRepository } from "../src/db/memories.ts";
import { ReminderRepository } from "../src/db/reminders.ts";
import { SkillRepository } from "../src/db/skills.ts";
import { SshCommandRepository } from "../src/db/ssh.ts";
import { WorkflowRepository } from "../src/db/workflows.ts";
import type { MediaServiceClient } from "../src/media/client.ts";
import type { SshClient } from "../src/ssh/client.ts";
import { context, testDatabase } from "./helpers.ts";

const open: ReturnType<typeof testDatabase>[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

function executor(
  db: MattDatabase,
  modelState = { value: "primary/model" },
  brain: BrainRepository | null = null,
  media: MediaServiceClient | null = null,
  directivePolicyCompiler: DirectivePolicyCompiler | null = {
    compileDirectivePolicy: async ({ instruction }) => ({
      version: 1,
      kind: instruction.startsWith("Do not respond")
        ? "delivery_suppression"
        : "response_constraint",
      delivery: instruction.startsWith("Do not respond") ? "suppress" : "normal",
      tools: instruction.startsWith("Do not respond") ? "block_all" : "normal",
      requirements: [instruction],
      summary: instruction,
    }),
  },
  ssh: SshClient | null = null,
): ToolExecutor {
  return new ToolExecutor(
    new MemoryRepository(db),
    new ReminderRepository(db),
    new SkillRepository(db),
    "UOWNER",
    db,
    new BackupManager(db, "/unused"),
    {
      getPrimaryModel: () => modelState.value,
      setPrimaryModel: (model) => {
        modelState.value = model;
      },
      resolveModel: async (model) => (model === "vendor/new-model" ? "vendor/new-model" : null),
    },
    "primary/model",
    brain,
    new ActionJournalRepository(db),
    undefined,
    media,
    undefined,
    undefined,
    undefined,
    directivePolicyCompiler,
    new WorkflowRepository(db),
    ssh,
    new SshCommandRepository(db),
  );
}

describe("model tools", () => {
  test("publishes the scoped local skill schemas", () => {
    expect(toolDefinitions.map((tool) => tool.function.name)).toEqual([
      "send_progress",
      "complete_scheduled_task_silently",
      "complete_turn_silently",
      "generate_image",
      "get_user_profile",
      "fetch_url",
      "inspect_media_service",
      "add_media",
      "manage_sonarr_episodes",
      "brain_list",
      "brain_search",
      "brain_read",
      "brain_export_map",
      "brain_save",
      "brain_remove_list_item",
      "save_memory",
      "list_memories",
      "delete_memory",
      "set_user_preferences",
      "list_user_preferences",
      "clear_user_preference",
      "create_temporary_directive",
      "list_temporary_directives",
      "resolve_temporary_directive",
      "create_reminder",
      "create_scheduled_task",
      "list_reminders",
      "cancel_reminder",
      "create_workflow",
      "list_workflows",
      "cancel_workflow",
      "get_recent_actions",
      "get_status",
      "list_skills",
      "load_skill",
      "propose_skill",
      "propose_skill_revision",
      "resolve_skill_proposal",
      "set_skill_state",
      "list_ssh_hosts",
      "propose_ssh_command",
      "resolve_ssh_command",
      "get_session_stats",
      "get_primary_model",
      "set_primary_model",
      "reset_primary_model",
    ]);
    expect(
      toolDefinitions.find((tool) => tool.function.name === "create_temporary_directive")?.function
        .parameters,
    ).toMatchObject({ required: ["instruction", "activation"] });
  });

  test("allows media inspection on authorized surfaces and keeps additions owner-only", async () => {
    const db = testDatabase();
    open.push(db);
    const calls: unknown[] = [];
    const media = {
      inspect: async (input: unknown) => {
        calls.push(input);
        return { service: "sonarr", view: "status" };
      },
      add: async (input: unknown) => {
        calls.push(input);
        return { service: "radarr", kind: "movie", added: true };
      },
    } as unknown as MediaServiceClient;
    const tools = executor(db, { value: "primary/model" }, null, media);
    const args = '{"service":"sonarr","view":"status"}';
    expect(await tools.execute("inspect_media_service", args, context())).toEqual({
      service: "sonarr",
      view: "status",
    });
    const addArgs = '{"kind":"movie","title":"Arrival","year":2016}';
    expect(() => tools.execute("add_media", addArgs, context())).toThrow("owner");
    expect(
      await tools.execute(
        "add_media",
        addArgs,
        context({ requesterId: "UOWNER", isOwner: true, surface: "channel" }),
      ),
    ).toEqual({ service: "radarr", kind: "movie", added: true });
    expect(calls).toEqual([
      { service: "sonarr", view: "status", limit: 20 },
      { kind: "movie", title: "Arrival", year: 2016, searchNow: true },
    ]);
  });

  test("keeps bounded Sonarr episode mutations in the owner's DM", async () => {
    const db = testDatabase();
    open.push(db);
    const calls: unknown[] = [];
    const media = {
      manageSonarrEpisodes: async (input: unknown) => {
        calls.push(input);
        return { service: "sonarr", action: "search_episodes", performed: true };
      },
    } as unknown as MediaServiceClient;
    const tools = executor(db, { value: "primary/model" }, null, media);
    const args = JSON.stringify({
      action: "search_episodes",
      series_title: "Severance",
      episodes: [{ season_number: 2, episode_number: 4 }],
    });
    expect(() => tools.execute("manage_sonarr_episodes", args, context())).toThrow("owner");
    expect(() =>
      tools.execute(
        "manage_sonarr_episodes",
        args,
        context({ requesterId: "UOWNER", isOwner: true, surface: "channel" }),
      ),
    ).toThrow("owner's DM");
    expect(
      await tools.execute(
        "manage_sonarr_episodes",
        args,
        context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" }),
      ),
    ).toMatchObject({ performed: true });
    expect(calls).toEqual([
      {
        action: "search_episodes",
        seriesTitle: "Severance",
        episodes: [{ seasonNumber: 2, episodeNumber: 4 }],
      },
    ]);

    const oversizedDelete = JSON.stringify({
      action: "delete_episode_files",
      series_title: "Severance",
      episodes: Array.from({ length: 21 }, (_, index) => ({
        season_number: 1,
        episode_number: index,
      })),
    });
    expect(() =>
      tools.execute(
        "manage_sonarr_episodes",
        oversizedDelete,
        context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" }),
      ),
    ).toThrow("limited to 20");
  });

  test("enforces surface-specific memory scope", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    expect(() =>
      tools.execute("save_memory", JSON.stringify({ scope: "user", text: "private" }), context()),
    ).toThrow("Only channel memory");
    const dm = context({ surface: "dm", channelId: "D1" });
    const saved = tools.execute(
      "save_memory",
      JSON.stringify({ scope: "user", text: "private" }),
      dm,
    ) as { text: string };
    expect(saved.text).toBe("private");
  });

  test("manages requester preferences and user-wide temporary directives", async () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const current = context({ workspaceId: "T123", requesterId: "U123" });
    const preferences = tools.execute(
      "set_user_preferences",
      JSON.stringify({ language: "Spanish", verbosity: "concise", units: "metric" }),
      current,
    ) as Array<{ key: string; value: string }>;
    expect(preferences.map((item) => [item.key, item.value])).toEqual([
      ["language", "Spanish"],
      ["units", "metric"],
      ["verbosity", "concise"],
    ]);
    expect(tools.execute("list_user_preferences", "{}", current)).toHaveLength(3);
    expect(
      tools.execute(
        "list_user_preferences",
        "{}",
        context({ workspaceId: "T123", requesterId: "UOTHER" }),
      ),
    ).toEqual([]);
    expect(
      tools.execute("clear_user_preference", JSON.stringify({ key: "units" }), current),
    ).toEqual({ deleted: true });

    const directive = (await tools.execute(
      "create_temporary_directive",
      JSON.stringify({
        instruction: "Do not respond until the report is finished.",
        activation: "now",
        release_phrase: "I finished the report",
      }),
      current,
    )) as { id: number; effect: string; scope: string; state: string };
    expect(directive).toMatchObject({ effect: "guidance", scope: "global", state: "active" });
    expect(tools.execute("list_temporary_directives", "{}", current)).toHaveLength(1);
    expect(
      tools.execute(
        "resolve_temporary_directive",
        JSON.stringify({ id: directive.id }),
        context({ workspaceId: "T123", requesterId: "UOTHER" }),
      ),
    ).toEqual({ resolved: false });
    expect(
      tools.execute("resolve_temporary_directive", JSON.stringify({ id: directive.id }), current),
    ).toEqual({ resolved: true });

    const scheduled = (await tools.execute(
      "create_temporary_directive",
      JSON.stringify({
        instruction: "Use concise replies during the review window.",
        activation: "scheduled",
        starts_at: "2099-01-01T09:00:00-05:00",
        expires_at: "2099-01-01T10:00:00-05:00",
      }),
      current,
    )) as { id: number; state: string; startsAt: number; expiresAt: number };
    expect(scheduled).toMatchObject({
      state: "scheduled",
      startsAt: Date.parse("2099-01-01T09:00:00-05:00"),
      expiresAt: Date.parse("2099-01-01T10:00:00-05:00"),
    });
    expect(tools.execute("list_temporary_directives", "{}", current)).toEqual([
      expect.objectContaining({ id: scheduled.id, state: "scheduled" }),
    ]);
    expect(() =>
      tools.execute(
        "create_temporary_directive",
        JSON.stringify({ instruction: "Start later.", activation: "scheduled" }),
        current,
      ),
    ).toThrow("starts_at is required");
    expect(() =>
      tools.execute(
        "create_temporary_directive",
        JSON.stringify({
          instruction: "Start now.",
          activation: "now",
          starts_at: "2099-01-01T09:00:00-05:00",
        }),
        current,
      ),
    ).toThrow("starts_at must be omitted");

    const blankOptionalDates = (await tools.execute(
      "create_temporary_directive",
      JSON.stringify({
        instruction: "Use concise replies until I finish reviewing.",
        activation: "now",
        release_phrase: "The requester says the review is finished.",
        starts_at: "",
        expires_at: "   ",
      }),
      current,
    )) as { id: number; state: string; startsAt: number; expiresAt: number | null };
    expect(blankOptionalDates).toMatchObject({ state: "active", expiresAt: null });
  });

  test("does not persist a temporary directive when policy compilation fails", async () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db, { value: "primary/model" }, null, null, {
      compileDirectivePolicy: async () => {
        throw new Error("policy provider unavailable");
      },
    });

    await expect(
      Promise.resolve(
        tools.execute(
          "create_temporary_directive",
          JSON.stringify({ instruction: "Ignore me for a while.", activation: "now" }),
          context(),
        ),
      ),
    ).rejects.toThrow("policy provider unavailable");
    expect(db.raw.query("SELECT count(*) AS count FROM temporary_directives").get()).toEqual({
      count: 0,
    });
  });

  test("routes Brain tools even when the default organization skill is unavailable", () => {
    const db = testDatabase();
    open.push(db);
    const current = context({ requesterId: "UOWNER", isOwner: true, surface: "dm" });
    expect(() => executor(db).execute("brain_search", '{"query":"architecture"}', current)).toThrow(
      "not configured",
    );

    const calls: string[] = [];
    const brain: BrainRepository = {
      list: (input) => {
        calls.push(`list:${input.limit}`);
        return { total: 0, notes: [] };
      },
      search: (input) => {
        calls.push(`search:${input.query}:${input.limit}`);
        return { results: [] };
      },
      read: (input) => {
        calls.push(`read:${input.path}`);
        return { content: "note" };
      },
      save: (input) => {
        calls.push(
          `save:${input.destinationKind}:${input.destinationTitle}:${input.entryKind}:${input.section}`,
        );
        return { saved: true };
      },
      removeListEntry: (input) => {
        calls.push(`remove:${input.destinationTitle}:${input.text}`);
        return { removed: true };
      },
      capture: (input) => {
        calls.push(`capture:${input.noteType}:${input.title}`);
        return { captured: true };
      },
      append: (input) => {
        calls.push(`append:${input.path}`);
        return { appended: true };
      },
    };
    const tools = executor(db, { value: "primary/model" }, brain);
    tools.execute("brain_list", "{}", current);
    tools.execute("brain_search", '{"query":"architecture"}', current);
    tools.execute("brain_read", '{"path":"wiki/concepts/architecture.md"}', current);
    const librarianId = new SkillRepository(db).catalog()[0]?.id ?? -1;
    new SkillRepository(db).setState({
      id: librarianId,
      state: "disabled",
      actorUserId: "UOWNER",
    });
    tools.execute(
      "brain_save",
      '{"destination_kind":"topic","destination_title":"Architecture","text":"Body","entry_kind":"prose","section":"Notes","topics":[]}',
      current,
    );
    new SkillRepository(db).setState({
      id: librarianId,
      state: "deleted",
      actorUserId: "UOWNER",
    });
    tools.execute(
      "brain_save",
      '{"destination_kind":"topic","destination_title":"Security","text":"Body","entry_kind":"prose","section":"Notes","topics":[]}',
      current,
    );
    tools.execute(
      "brain_remove_list_item",
      '{"destination_title":"To Do","text":"Look into AppleCare"}',
      current,
    );
    expect(calls).toEqual([
      "list:20",
      "search:architecture:5",
      "read:wiki/concepts/architecture.md",
      "save:topic:Architecture:prose:Notes",
      "save:topic:Security:prose:Notes",
      "remove:To Do:Look into AppleCare",
    ]);
  });

  test("creates, lists, and cancels requester reminders without model ambiguity", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const current = context();
    const created = tools.execute(
      "create_reminder",
      JSON.stringify({
        text: "Do the thing",
        first_run_at: "2099-01-01T09:00:00",
        timezone: "America/New_York",
        recurrence: "weekly",
        delivery: "thread",
        notification_title: "Weekly Thing Reminder",
      }),
      current,
    ) as { id: number };
    const scheduled = tools.execute(
      "create_scheduled_task",
      JSON.stringify({
        command: "Read my current to-do list and recommend two priorities",
        first_run_at: "2099-01-02T09:00:00",
        timezone: "America/New_York",
        recurrence: "daily",
        delivery: "channel",
        notification_title: "Daily Priority Brief",
        presentation_instructions:
          "Lead with two priorities in ranked order, then one short reason for each.",
      }),
      current,
    ) as { id: number; kind: string; text: string };
    expect(scheduled).toMatchObject({
      kind: "agent_task",
      text: "Read my current to-do list and recommend two priorities",
      delivery_mode: "channel",
      notification_title: "Daily Priority Brief",
      presentation_instructions:
        "Lead with two priorities in ranked order, then one short reason for each.",
    });
    expect(tools.execute("list_reminders", "{}", current)).toMatchObject([
      { id: created.id, kind: "reminder" },
      { id: scheduled.id, kind: "agent_task" },
    ]);
    expect(tools.execute("cancel_reminder", JSON.stringify({ id: created.id }), current)).toEqual({
      cancelled: true,
      kind: "reminder",
    });
  });

  test("requires a channel delivery choice and defaults DM schedules to DM", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const args = {
      text: "Choose a destination",
      first_run_at: "2099-01-01T09:00:00",
      timezone: "America/New_York",
      recurrence: "once",
    };
    expect(() => tools.execute("create_reminder", JSON.stringify(args), context())).toThrow(
      "Ask whether the future message should be posted",
    );
    expect(
      tools.execute(
        "create_reminder",
        JSON.stringify(args),
        context({ surface: "dm", channelId: "D123" }),
      ),
    ).toMatchObject({ delivery_mode: "dm" });
    expect(() =>
      tools.execute(
        "create_reminder",
        JSON.stringify({ ...args, delivery: "channel" }),
        context({ surface: "dm", channelId: "D123" }),
      ),
    ).toThrow("only be selected from the destination channel");
  });

  test("creates, lists, and cancels an owner-only event-driven workflow", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const argumentsJson = JSON.stringify({
      name: "Trimmer proof",
      activation: "scheduled",
      starts_at: "2099-01-01T09:00:00-05:00",
      timezone: "America/New_York",
      expires_at: "2099-01-01T10:00:00-05:00",
      delivery: "dm",
      cancel_message: "Barrage called off.",
      completion_policy: {
        kind: "trusted_event_count",
        event: "brain_list_item_removed",
        destination_title: "To Do",
        target: 2,
        completion_node: "done",
        summary: "Complete after two successful To Do removals.",
      },
      start_node: "prompt",
      nodes: [
        { id: "prompt", type: "message", text: "Send proof.", next: "wait" },
        {
          id: "wait",
          type: "await",
          timeout_seconds: 30,
          on_timeout: "nag",
          matches: [{ condition: "Image shows a trimmer.", evidence: "image", next: "done" }],
        },
        {
          id: "nag",
          type: "repeat",
          messages: ["Proof, please."],
          interval_seconds: 5,
          max_occurrences: 10,
          matches: [{ condition: "Image shows a trimmer.", evidence: "image", next: "done" }],
          on_exhausted: "done",
        },
        { id: "done", type: "complete", message: "Finished." },
      ],
    });
    expect(() => tools.execute("create_workflow", argumentsJson, context())).toThrow("owner");
    const owner = context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" });
    const created = tools.execute("create_workflow", argumentsJson, owner) as {
      id: number;
      state: string;
      messageSafetyCap: number;
    };
    expect(created).toMatchObject({ state: "scheduled", messageSafetyCap: 500 });
    expect(
      tools.execute(
        "list_workflows",
        "{}",
        context({
          requesterId: "UOWNER",
          isOwner: true,
          surface: "channel",
          channelId: "COTHER",
        }),
      ),
    ).toEqual([
      expect.objectContaining({
        id: created.id,
        name: "Trimmer proof",
        sentMessages: 0,
        completion: {
          summary: "Complete after two successful To Do removals.",
          current: 0,
          target: 2,
        },
      }),
    ]);
    expect(
      tools.execute(
        "cancel_workflow",
        JSON.stringify({ id: created.id }),
        context({ requesterId: "UOTHER" }),
      ),
    ).toEqual({ cancelled: false });
    expect(tools.execute("cancel_workflow", JSON.stringify({ id: created.id }), owner)).toEqual({
      cancelled: true,
    });
    expect(
      db.raw.query("SELECT status, deleted_at FROM workflows WHERE id = ?").get(created.id),
    ).toEqual({
      status: "cancelled",
      deleted_at: expect.any(Number),
    });
  });

  test("turns successful Brain removals into deduplicated trusted workflow progress", () => {
    const db = testDatabase();
    open.push(db);
    const brain: BrainRepository = {
      list: () => ({}),
      search: () => ({}),
      read: () => ({}),
      save: () => ({}),
      removeListEntry: () => ({ removed: true, destination: { kind: "list", title: "To Do" } }),
      capture: () => ({}),
      append: () => ({}),
    };
    const tools = executor(db, { value: "primary/model" }, brain);
    const owner = context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" });
    tools.execute(
      "create_workflow",
      JSON.stringify({
        name: "Finish two tasks",
        activation: "now",
        timezone: "UTC",
        expires_at: new Date(Date.now() + 60 * 60 * 1_000).toISOString(),
        delivery: "dm",
        completion_policy: {
          kind: "trusted_event_count",
          event: "brain_list_item_removed",
          destination_title: "To Do",
          target: 2,
          completion_node: "done",
          summary: "Complete after two successful To Do removals.",
        },
        start_node: "wait",
        nodes: [
          {
            id: "wait",
            type: "await",
            matches: [
              { condition: "Matt says two tasks are done.", evidence: "text", next: "done" },
            ],
          },
          { id: "done", type: "complete", message: "Two tasks completed." },
        ],
      }),
      owner,
    );

    const firstArgs = JSON.stringify({ destination_title: "To Do", text: "First task" });
    expect(tools.execute("brain_remove_list_item", firstArgs, owner)).toMatchObject({
      removed: true,
      workflowProgress: [
        {
          name: "Finish two tasks",
          current: 1,
          target: 2,
          completed: false,
        },
      ],
    });
    expect(tools.execute("brain_remove_list_item", firstArgs, owner)).not.toHaveProperty(
      "workflowProgress",
    );
    expect(
      tools.execute(
        "brain_remove_list_item",
        JSON.stringify({ destination_title: "To Do", text: "Second task" }),
        context({ ...owner, turnId: "Ev2" }),
      ),
    ).toMatchObject({
      workflowProgress: [expect.objectContaining({ current: 2, target: 2, completed: true })],
    });
    expect(db.raw.query("SELECT current_node_id, state_json FROM workflows").get()).toEqual({
      current_node_id: "done",
      state_json: JSON.stringify({ trustedEventCounts: { brain_list_item_removed: 2 } }),
    });
    expect(db.raw.query("SELECT event_key FROM workflow_events ORDER BY id").all()).toHaveLength(2);
    expect(JSON.stringify(db.raw.query("SELECT * FROM workflow_events").all())).not.toContain(
      "First task",
    );
  });

  test("requires a stable presentation contract for recurring schedules", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const common = {
      first_run_at: "2099-01-01T09:00:00",
      timezone: "America/New_York",
      recurrence: "daily",
      delivery: "thread",
    };
    expect(() =>
      tools.execute(
        "create_reminder",
        JSON.stringify({ ...common, text: "Take a walk" }),
        context(),
      ),
    ).toThrow("task-specific notification title");
    expect(() =>
      tools.execute(
        "create_scheduled_task",
        JSON.stringify({
          ...common,
          command: "Check for new reviews",
          notification_title: "Daily Review Watch",
        }),
        context(),
      ),
    ).toThrow("stable presentation instructions");
  });

  test("exposes only sanitized actions from the exact Slack thread and requester", () => {
    const db = testDatabase();
    open.push(db);
    const current = context();
    const actions = new ActionJournalRepository(db);
    actions.recordToolCall({
      toolName: "brain_save",
      toolResult: { ok: true, result: { path: "secret/path.md" } },
      context: current,
      now: Date.UTC(2026, 7, 24, 4, 0, 0),
    });
    actions.recordToolCall({
      toolName: "brain_save",
      toolResult: { ok: true, result: {} },
      context: context({ requesterId: "UOTHER" }),
      now: Date.UTC(2026, 7, 24, 4, 1, 0),
    });

    const result = executor(db).execute("get_recent_actions", "{}", current);
    expect(result).toEqual({
      trusted: true,
      scope: "current_slack_thread_and_requester",
      note: "Sanitized tool activity only. Raw arguments, content, results, and action target IDs are not retained.",
      actions: [
        {
          tool: "brain_save",
          outcome: "succeeded",
          summary: "Saved knowledge to the scoped Brain",
          occurredAt: "2026-08-24T04:00:00.000Z",
        },
      ],
    });
    expect(JSON.stringify(result)).not.toContain("private launch code");
    expect(JSON.stringify(result)).not.toContain("secret/path.md");
    expect(JSON.stringify(result)).not.toContain("UOTHER");
  });

  test("exposes status and enforces owner-only persistent model changes", async () => {
    const db = testDatabase();
    open.push(db);
    const modelState = { value: "primary/model" };
    const tools = executor(db, modelState);
    const other = context({ requesterId: "UOTHER", isOwner: false });
    const owner = context({ requesterId: "UOWNER", isOwner: true });

    expect(tools.execute("get_status", "{}", other)).toMatchObject({ running: true });
    expect(tools.execute("get_primary_model", "{}", other)).toEqual({
      model: "primary/model",
    });
    expect(() => tools.execute("set_primary_model", '{"model":"vendor/new-model"}', other)).toThrow(
      "Only the FigAi owner",
    );
    expect(modelState.value).toBe("primary/model");
    expect(await tools.execute("set_primary_model", '{"model":"vendor/new-model"}', owner)).toEqual(
      {
        model: "vendor/new-model",
        changed: true,
      },
    );
    expect(db.getSetting("primary_model")).toBe("vendor/new-model");
    expect(tools.execute("reset_primary_model", "{}", owner)).toEqual({
      model: "primary/model",
      reset: true,
    });
    expect(db.getSetting("primary_model")).toBeNull();
  });

  test("exposes recorded usage for the current Slack thread", () => {
    const db = testDatabase();
    open.push(db);
    const current = context();
    db.recordInteraction({
      workspaceId: current.workspaceId,
      channelId: current.channelId,
      threadTs: current.threadTs,
      requesterId: current.requesterId,
      surface: current.surface,
      model: "vendor/first",
      latencyMs: 100,
      promptTokens: 20,
      completionTokens: 5,
      totalTokens: 25,
      reportedCost: 0.01,
      tools: [],
      status: "ok",
      createdAt: 1,
    });
    db.recordInteraction({
      workspaceId: current.workspaceId,
      channelId: current.channelId,
      threadTs: current.threadTs,
      requesterId: current.requesterId,
      surface: current.surface,
      model: "vendor/latest",
      latencyMs: 200,
      promptTokens: 40,
      completionTokens: 10,
      totalTokens: 50,
      reportedCost: 0.02,
      tools: ["openrouter:web_search"],
      status: "ok",
      createdAt: 2,
    });
    db.recordInteraction({
      workspaceId: current.workspaceId,
      channelId: "COTHER",
      threadTs: current.threadTs,
      requesterId: current.requesterId,
      surface: current.surface,
      totalTokens: 999,
      reportedCost: 9.99,
      status: "ok",
      createdAt: 3,
    });

    const result = executor(db).execute("get_session_stats", "{}", current) as {
      totals: { completedTurns: number; totalTokens: number; reportedCostUsd: number };
      latestCompletedTurn: { model: string; tools: string[] };
    };
    expect(result.totals).toMatchObject({
      completedTurns: 2,
      totalTokens: 75,
      reportedCostUsd: 0.03,
    });
    expect(result.latestCompletedTurn).toMatchObject({
      model: "vendor/latest",
      tools: ["openrouter:web_search"],
    });
  });

  test("guards global skill management while allowing enabled skill use", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const other = context({ requesterId: "UOTHER", isOwner: false, turnId: "Ev1" });
    const owner = context({ requesterId: "UOWNER", isOwner: true, turnId: "Ev1" });
    const draftArguments = JSON.stringify({
      name: "Release notes",
      description: "Creates concise release notes from a change list",
      instructions: "Group changes by user impact and omit internal implementation trivia.",
    });
    expect(() => tools.execute("propose_skill", draftArguments, other)).toThrow(
      "Only the FigAi owner",
    );
    const proposal = tools.execute("propose_skill", draftArguments, owner) as {
      proposalId: number;
      requiresLaterConfirmation: boolean;
    };
    expect(proposal.requiresLaterConfirmation).toBeTrue();
    const proposalDefinition = toolDefinitions.find(
      (tool) => tool.function.name === "propose_skill",
    );
    expect(proposalDefinition?.function.parameters.properties).not.toHaveProperty("skill_id");
    expect(() =>
      tools.execute("resolve_skill_proposal", JSON.stringify({ decision: "confirm" }), owner),
    ).toThrow("later message");
    const confirmed = tools.execute(
      "resolve_skill_proposal",
      JSON.stringify({ decision: "confirm" }),
      { ...owner, turnId: "Ev2" },
    ) as { skill: { id: number } };
    expect(tools.execute("list_skills", "{}", other)).toHaveLength(2);
    expect(
      tools.execute(
        "propose_skill_revision",
        JSON.stringify({
          skill_id: confirmed.skill.id,
          name: "Release notes",
          description: "Creates short release notes from a change list",
          instructions: "Lead with user impact and keep the result under 100 words.",
        }),
        context({ requesterId: "UOWNER", isOwner: true, turnId: "Ev3" }),
      ),
    ).toMatchObject({ operation: "update", targetSkillId: confirmed.skill.id });
    expect(
      tools.execute("load_skill", JSON.stringify({ id: confirmed.skill.id }), other),
    ).toMatchObject({ untrusted: true, name: "Release notes" });
    expect(() =>
      tools.execute(
        "set_skill_state",
        JSON.stringify({ id: confirmed.skill.id, state: "disabled" }),
        other,
      ),
    ).toThrow("Only the FigAi owner");
    tools.execute(
      "set_skill_state",
      JSON.stringify({ id: confirmed.skill.id, state: "disabled" }),
      owner,
    );
    expect(() =>
      tools.execute("load_skill", JSON.stringify({ id: confirmed.skill.id }), owner),
    ).toThrow("unavailable");
    expect(() => tools.execute("list_skills", '{"include_disabled":true}', other)).toThrow(
      "Only the FigAi owner",
    );
  });
});

describe("ssh commands", () => {
  function fakeSsh(
    aliases: string[],
    run: (
      alias: string,
      command: string,
    ) => Promise<{
      exitCode: number | null;
      timedOut: boolean;
      stdout: string;
      stderr: string;
      stdoutTruncated: boolean;
      stderrTruncated: boolean;
    }>,
  ): SshClient {
    return { aliases: () => aliases, run } as unknown as SshClient;
  }

  test("is unavailable when no SSH hosts are configured", () => {
    const db = testDatabase();
    open.push(db);
    const tools = executor(db);
    const owner = context({ requesterId: "UOWNER", isOwner: true });
    expect(() => tools.execute("list_ssh_hosts", "{}", owner)).toThrow("not configured");
    expect(() =>
      tools.execute(
        "propose_ssh_command",
        JSON.stringify({ host_alias: "nas", command: "uptime" }),
        owner,
      ),
    ).toThrow("not configured");
  });

  test("restricts every SSH tool to the owner", () => {
    const db = testDatabase();
    open.push(db);
    const ssh = fakeSsh(["nas"], async () => {
      throw new Error("should not run");
    });
    const tools = executor(db, { value: "primary/model" }, null, null, undefined, ssh);
    const other = context({ requesterId: "U999", isOwner: false });
    expect(() => tools.execute("list_ssh_hosts", "{}", other)).toThrow("Only the FigAi owner");
    expect(() =>
      tools.execute(
        "propose_ssh_command",
        JSON.stringify({ host_alias: "nas", command: "uptime" }),
        other,
      ),
    ).toThrow("Only the FigAi owner");
    expect(() =>
      tools.execute("resolve_ssh_command", JSON.stringify({ decision: "confirm" }), other),
    ).toThrow("Only the FigAi owner");
  });

  test("lists configured aliases and rejects an unknown one", () => {
    const db = testDatabase();
    open.push(db);
    const ssh = fakeSsh(["nas", "homelab"], async () => {
      throw new Error("should not run");
    });
    const tools = executor(db, { value: "primary/model" }, null, null, undefined, ssh);
    const owner = context({ requesterId: "UOWNER", isOwner: true });
    expect(tools.execute("list_ssh_hosts", "{}", owner)).toEqual({ hosts: ["nas", "homelab"] });
    expect(() =>
      tools.execute(
        "propose_ssh_command",
        JSON.stringify({ host_alias: "unknown", command: "uptime" }),
        owner,
      ),
    ).toThrow("No SSH host is configured");
  });

  test("requires a later same-thread confirmation before executing, then audits the run", async () => {
    const db = testDatabase();
    open.push(db);
    const calls: Array<{ alias: string; command: string }> = [];
    const ssh = fakeSsh(["nas"], async (alias, command) => {
      calls.push({ alias, command });
      return {
        exitCode: 0,
        timedOut: false,
        stdout: "up 3 days\n",
        stderr: "",
        stdoutTruncated: false,
        stderrTruncated: false,
      };
    });
    const tools = executor(db, { value: "primary/model" }, null, null, undefined, ssh);
    const owner = context({ requesterId: "UOWNER", isOwner: true, turnId: "Ev1" });
    const draft = tools.execute(
      "propose_ssh_command",
      JSON.stringify({ host_alias: "nas", command: "uptime" }),
      owner,
    ) as { proposalId: number; hostAlias: string; command: string };
    expect(draft).toMatchObject({
      hostAlias: "nas",
      command: "uptime",
      requiresLaterConfirmation: true,
    });
    expect(calls).toHaveLength(0);

    expect(() =>
      tools.execute("resolve_ssh_command", JSON.stringify({ decision: "confirm" }), owner),
    ).toThrow("later message");
    expect(calls).toHaveLength(0);

    const result = (await tools.execute(
      "resolve_ssh_command",
      JSON.stringify({ decision: "confirm" }),
      { ...owner, turnId: "Ev2" },
    )) as { confirmed: boolean; exitCode: number; stdout: string; untrusted: boolean };
    expect(calls).toEqual([{ alias: "nas", command: "uptime" }]);
    expect(result).toMatchObject({
      confirmed: true,
      hostAlias: "nas",
      command: "uptime",
      exitCode: 0,
      timedOut: false,
      stdout: "up 3 days\n",
      untrusted: true,
    });
    expect(
      db.raw.query("SELECT host_alias, command, exit_code, timed_out FROM ssh_command_audit").get(),
    ).toEqual({ host_alias: "nas", command: "uptime", exit_code: 0, timed_out: 0 });
    expect(() => db.raw.query("DELETE FROM ssh_command_audit").run()).toThrow("immutable");
  });

  test("cancelling a draft never runs it", () => {
    const db = testDatabase();
    open.push(db);
    const calls: unknown[] = [];
    const ssh = fakeSsh(["nas"], async (alias, command) => {
      calls.push({ alias, command });
      return {
        exitCode: 0,
        timedOut: false,
        stdout: "",
        stderr: "",
        stdoutTruncated: false,
        stderrTruncated: false,
      };
    });
    const tools = executor(db, { value: "primary/model" }, null, null, undefined, ssh);
    const owner = context({ requesterId: "UOWNER", isOwner: true, turnId: "Ev1" });
    tools.execute(
      "propose_ssh_command",
      JSON.stringify({ host_alias: "nas", command: "rm -rf /data" }),
      owner,
    );
    expect(
      tools.execute("resolve_ssh_command", JSON.stringify({ decision: "cancel" }), {
        ...owner,
        turnId: "Ev2",
      }),
    ).toEqual({ confirmed: false, cancelled: true });
    expect(calls).toHaveLength(0);
  });
});
