import type { RuntimeContext } from "../types.ts";
import type { MattDatabase } from "./database.ts";

type ActionOutcome = "succeeded" | "failed" | "no_change";

interface ActionJournalRow {
  tool_name: string;
  outcome: ActionOutcome;
  summary: string;
  scheduled_for: number | null;
  occurred_at: number;
}

interface ActionDescription {
  outcome: ActionOutcome;
  summary: string;
  scheduledFor?: number;
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function integer(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) ? value : undefined;
}

const SUCCESS_SUMMARIES: Readonly<Record<string, string>> = {
  "openrouter:web_search": "Searched the web",
  fetch_url: "Fetched an explicitly supplied public URL",
  inspect_media_service: "Inspected a configured local media service",
  add_media: "Added requested media to a configured local service",
  manage_sonarr_episodes: "Managed explicitly selected Sonarr episodes",
  send_progress: "Sent a progress update",
  complete_scheduled_task_silently: "Completed a scheduled task without a message",
  complete_turn_silently: "Completed a Slack turn without a message",
  directive_compliance: "Corrected a response that violated an active directive",
  directive_ingress: "Evaluated an active directive before a turn",
  workflow_ingress: "Evaluated active workflow conditions before a turn",
  generate_image: "Generated an image",
  get_user_profile: "Retrieved an authorized Slack profile",
  brain_list: "Listed notes in the scoped Brain",
  brain_search: "Searched the scoped Brain",
  brain_read: "Read a note from the scoped Brain",
  brain_export_map: "Exported a scoped Brain map",
  brain_save: "Saved knowledge to the scoped Brain",
  brain_remove_list_item: "Removed an explicitly requested Brain list item",
  save_memory: "Saved a scoped memory",
  list_memories: "Listed scoped memories",
  delete_memory: "Deleted a scoped memory",
  set_user_preferences: "Updated requester response preferences",
  list_user_preferences: "Listed requester response preferences",
  clear_user_preference: "Cleared a requester response preference",
  create_temporary_directive: "Created a temporary requester directive",
  list_temporary_directives: "Listed active requester directives",
  resolve_temporary_directive: "Resolved a temporary requester directive",
  list_reminders: "Listed active reminders and scheduled tasks",
  create_workflow: "Created a durable event-driven workflow",
  list_workflows: "Listed active durable workflows",
  cancel_workflow: "Cancelled a durable workflow",
  get_recent_actions: "Inspected recent tool activity",
  get_status: "Checked FigAi service status",
  list_skills: "Listed instruction skills",
  load_skill: "Loaded an instruction skill",
  propose_skill: "Created an instruction-skill draft",
  propose_skill_revision: "Created an instruction-skill revision draft",
  resolve_skill_proposal: "Resolved an instruction-skill draft",
  set_skill_state: "Changed an instruction skill's state",
  get_session_stats: "Inspected current-thread usage",
  get_primary_model: "Inspected the primary model",
  set_primary_model: "Changed the primary model",
  reset_primary_model: "Reset the primary model",
  list_ssh_hosts: "Listed configured SSH host aliases",
  propose_ssh_command: "Drafted an SSH command for later confirmation",
};

const KNOWN_TOOL_NAMES = new Set([
  ...Object.keys(SUCCESS_SUMMARIES),
  "create_reminder",
  "create_scheduled_task",
  "cancel_reminder",
  "create_workflow",
  "cancel_workflow",
  "resolve_ssh_command",
]);

function safeToolName(toolName: string): string {
  return KNOWN_TOOL_NAMES.has(toolName) ? toolName : "unknown_tool";
}

function descriptionFor(toolName: string, toolResult: unknown): ActionDescription {
  const envelope = object(toolResult);
  const result = object(envelope.result);
  const succeeded = envelope.ok === true;
  if (toolName === "directive_compliance") {
    if (!succeeded || result.outcome === "unavailable") {
      return { outcome: "failed", summary: "Directive verifier failed closed" };
    }
    return {
      outcome: "succeeded",
      summary:
        result.action === "suppress"
          ? "Suppressed a response under an active directive"
          : result.action === "retry"
            ? "Regenerated a response under an active directive"
            : "Verified a response against active directives",
    };
  }
  if (toolName === "directive_ingress") {
    if (!succeeded || result.outcome === "unavailable") {
      return { outcome: "failed", summary: "Directive ingress judge failed closed" };
    }
    const satisfiedCount = integer(result.satisfiedCount) ?? 0;
    const bypassCount = integer(result.bypassCount) ?? 0;
    return {
      outcome: "succeeded",
      summary:
        satisfiedCount > 0
          ? "Released a satisfied temporary directive"
          : bypassCount > 0
            ? "Allowed explicit directive management for one turn"
            : "Kept active temporary directives in force",
    };
  }
  if (toolName === "workflow_ingress") {
    if (!succeeded || result.outcome === "unavailable") {
      return { outcome: "failed", summary: "Workflow condition judge was unavailable" };
    }
    const matchedCount = integer(result.matchedCount) ?? 0;
    const cancelledCount = integer(result.cancelledCount) ?? 0;
    return {
      outcome: "succeeded",
      summary:
        cancelledCount > 0
          ? "Cancelled a workflow from an explicit requester message"
          : matchedCount > 0
            ? "Advanced a workflow from matching requester evidence"
            : "Kept active workflows waiting",
    };
  }
  if (!succeeded) {
    return { outcome: "failed", summary: `Tool call failed: ${toolName}` };
  }

  if (toolName === "create_reminder" || toolName === "create_scheduled_task") {
    const scheduledTask = toolName === "create_scheduled_task";
    const scheduledFor = integer(result.next_run_at);
    return {
      outcome: "succeeded",
      summary: scheduledTask ? "Created a scheduled task" : "Created a reminder",
      ...(scheduledFor === undefined ? {} : { scheduledFor }),
    };
  }

  if (toolName === "cancel_reminder") {
    const kind = result.kind === "agent_task" ? "scheduled_task" : "reminder";
    if (result.cancelled !== true) {
      return {
        outcome: "no_change",
        summary: "A schedule cancellation request made no change",
      };
    }
    return {
      outcome: "succeeded",
      summary: kind === "scheduled_task" ? "Cancelled a scheduled task" : "Cancelled a reminder",
    };
  }

  if (toolName === "create_workflow") {
    const scheduledFor = integer(result.nextRunAt);
    return {
      outcome: "succeeded",
      summary: "Created a durable event-driven workflow",
      ...(scheduledFor === undefined ? {} : { scheduledFor }),
    };
  }

  if (toolName === "cancel_workflow" && result.cancelled !== true) {
    return { outcome: "no_change", summary: "A workflow cancellation request made no change" };
  }

  if (toolName === "delete_memory" && result.deleted !== true) {
    return { outcome: "no_change", summary: "A memory deletion request made no change" };
  }

  if (toolName === "clear_user_preference" && result.deleted !== true) {
    return { outcome: "no_change", summary: "A preference deletion request made no change" };
  }

  if (toolName === "resolve_temporary_directive" && result.resolved !== true) {
    return { outcome: "no_change", summary: "A directive resolution request made no change" };
  }

  if (toolName === "add_media" && result.added !== true) {
    return { outcome: "no_change", summary: "A media addition request made no change" };
  }

  if (toolName === "resolve_ssh_command") {
    if (result.cancelled === true) {
      return { outcome: "no_change", summary: "Cancelled a drafted SSH command" };
    }
    if (result.confirmed !== true) {
      return { outcome: "failed", summary: "SSH command confirmation failed" };
    }
    if (result.timedOut === true) {
      return { outcome: "failed", summary: "An SSH command timed out" };
    }
    const exitCode = integer(result.exitCode);
    if (exitCode !== undefined && exitCode !== 0) {
      return { outcome: "failed", summary: `An SSH command exited with status ${exitCode}` };
    }
    return { outcome: "succeeded", summary: "Ran an SSH command on a configured host" };
  }

  if (toolName === "manage_sonarr_episodes") {
    if (result.performed !== true) {
      return { outcome: "no_change", summary: "A Sonarr episode request made no change" };
    }
    if (result.action === "delete_episode_files") {
      return { outcome: "succeeded", summary: "Deleted selected Sonarr episode files" };
    }
    return {
      outcome: "succeeded",
      summary:
        result.action === "search_season"
          ? "Queued a selected Sonarr season search"
          : "Queued selected Sonarr episode searches",
    };
  }

  return {
    outcome: "succeeded",
    summary: SUCCESS_SUMMARIES[toolName] ?? `Completed tool call: ${toolName}`,
  };
}

export class ActionJournalRepository {
  constructor(private readonly db: MattDatabase) {}

  recordToolCall(input: {
    toolName: string;
    toolResult: unknown;
    context: RuntimeContext;
    now?: number;
  }): void {
    const toolName = safeToolName(input.toolName);
    const description = descriptionFor(toolName, input.toolResult);
    this.db.raw
      .query(`
        INSERT INTO action_journal(
          workspace_id, channel_id, thread_ts, requester_id,
          tool_name, outcome, summary, scheduled_for, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        input.context.workspaceId,
        input.context.channelId,
        input.context.threadTs,
        input.context.requesterId,
        toolName,
        description.outcome,
        description.summary,
        description.scheduledFor ?? null,
        input.now ?? Date.now(),
      );
  }

  list(input: { context: RuntimeContext; limit: number }): unknown {
    const limit = Math.max(1, Math.min(20, Math.trunc(input.limit)));
    const rows = this.db.raw
      .query<ActionJournalRow, [string, string, string, string, number]>(`
        SELECT tool_name, outcome, summary, scheduled_for, occurred_at
        FROM action_journal
        WHERE workspace_id = ? AND channel_id = ? AND thread_ts = ? AND requester_id = ?
        ORDER BY occurred_at DESC, id DESC
        LIMIT ?
      `)
      .all(
        input.context.workspaceId,
        input.context.channelId,
        input.context.threadTs,
        input.context.requesterId,
        limit,
      );
    return {
      trusted: true,
      scope: "current_slack_thread_and_requester",
      note: "Sanitized tool activity only. Raw arguments, content, results, and action target IDs are not retained.",
      actions: rows.map((row) => ({
        tool: row.tool_name,
        outcome: row.outcome,
        summary: row.summary,
        occurredAt: new Date(row.occurred_at).toISOString(),
        ...(row.scheduled_for === null
          ? {}
          : { scheduledFor: new Date(row.scheduled_for).toISOString() }),
      })),
    };
  }
}
