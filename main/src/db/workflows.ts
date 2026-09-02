import { randomUUID } from "node:crypto";
import { z } from "zod";
import type { ModelContentPart } from "../files.ts";
import type { RuntimeContext, ScheduleDelivery, SlackFile } from "../types.ts";
import type { MattDatabase } from "./database.ts";

export const MAX_WORKFLOW_MESSAGES = 500;
export const MAX_WORKFLOW_LIFETIME_MS = 7 * 24 * 60 * 60 * 1_000;

const nodeId = z
  .string()
  .trim()
  .min(1)
  .max(32)
  .regex(/^[a-z][a-z0-9_-]*$/);
const evidence = z.enum(["text", "image", "attachment", "any"]);

export const workflowCompletionPolicySchema = z.object({
  kind: z.literal("trusted_event_count"),
  event: z.literal("brain_list_item_removed"),
  destination_title: z.string().trim().min(1).max(120),
  target: z.number().int().min(1).max(100),
  completion_node: nodeId,
  summary: z.string().trim().min(1).max(300),
});

export const workflowMatchSchema = z.object({
  condition: z.string().trim().min(1).max(500),
  evidence,
  next: nodeId,
});

const messageNode = z.object({
  id: nodeId,
  type: z.literal("message"),
  text: z.string().trim().min(1).max(1_000),
  next: nodeId,
});

const delayNode = z.object({
  id: nodeId,
  type: z.literal("delay"),
  seconds: z.number().int().min(1).max(604_800),
  next: nodeId,
});

const awaitNode = z.object({
  id: nodeId,
  type: z.literal("await"),
  matches: z.array(workflowMatchSchema).min(1).max(5),
  timeout_seconds: z.number().int().min(1).max(604_800).optional(),
  on_timeout: nodeId.optional(),
});

const repeatNode = z.object({
  id: nodeId,
  type: z.literal("repeat"),
  messages: z.array(z.string().trim().min(1).max(1_000)).min(1).max(20),
  interval_seconds: z.number().int().min(5).max(86_400),
  matches: z.array(workflowMatchSchema).min(1).max(5),
  max_occurrences: z.number().int().min(1).max(MAX_WORKFLOW_MESSAGES).optional(),
  on_exhausted: nodeId.optional(),
});

const completeNode = z.object({
  id: nodeId,
  type: z.literal("complete"),
  message: z.string().trim().min(1).max(1_000).optional(),
});

export const workflowNodeSchema = z.discriminatedUnion("type", [
  messageNode,
  delayNode,
  awaitNode,
  repeatNode,
  completeNode,
]);

export const workflowPlanSchema = z
  .object({
    start_node: nodeId,
    nodes: z.array(workflowNodeSchema).min(2).max(30),
  })
  .superRefine((plan, ctx) => {
    const ids = new Set<string>();
    for (const node of plan.nodes) {
      if (
        node.type === "await" &&
        (node.timeout_seconds === undefined) !== (node.on_timeout === undefined)
      ) {
        ctx.addIssue({
          code: "custom",
          message: `Await node ${node.id} requires timeout_seconds and on_timeout together.`,
        });
      }
      if (ids.has(node.id)) {
        ctx.addIssue({ code: "custom", message: `Duplicate workflow node: ${node.id}.` });
      }
      ids.add(node.id);
    }
    if (!ids.has(plan.start_node)) {
      ctx.addIssue({ code: "custom", message: "The workflow start_node does not exist." });
    }
    const targets: string[] = [];
    for (const node of plan.nodes) {
      if (node.type === "message" || node.type === "delay") targets.push(node.next);
      if (node.type === "await") {
        targets.push(...node.matches.map((match) => match.next));
        if (node.on_timeout) targets.push(node.on_timeout);
      }
      if (node.type === "repeat") {
        targets.push(...node.matches.map((match) => match.next));
        if (node.on_exhausted) targets.push(node.on_exhausted);
      }
    }
    for (const target of targets) {
      if (!ids.has(target)) {
        ctx.addIssue({ code: "custom", message: `Workflow target does not exist: ${target}.` });
      }
    }
  });

export type WorkflowNode = z.infer<typeof workflowNodeSchema>;
export type WorkflowPlan = z.infer<typeof workflowPlanSchema>;
export type WorkflowEvidence = z.infer<typeof evidence>;
export type WorkflowCompletionPolicy = z.infer<typeof workflowCompletionPolicySchema>;
export type WorkflowStatus =
  | "scheduled"
  | "active"
  | "completed"
  | "cancelled"
  | "expired"
  | "failed";

export interface WorkflowRecord {
  id: number;
  workspace_id: string;
  creator_user_id: string;
  channel_id: string;
  thread_ts: string;
  surface: "dm" | "channel";
  delivery_mode: ScheduleDelivery;
  name: string;
  plan_json: string;
  completion_policy_json: string | null;
  state_json: string;
  current_node_id: string;
  node_entered_at: number;
  iteration: number;
  message_count: number;
  starts_at: number;
  next_run_at: number | null;
  expires_at: number;
  status: WorkflowStatus;
  cancel_message: string | null;
  lease_token: string | null;
  lease_expires_at: number | null;
  last_error: string | null;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  finished_reason: string | null;
  deleted_at: number | null;
}

export interface WorkflowCompletionProgress {
  name: string;
  summary: string;
  current: number;
  target: number;
  completed: boolean;
}

export interface WorkflowIngressCandidate {
  workflowId: number;
  name: string;
  matches: Array<{ index: number; condition: string; evidence: WorkflowEvidence }>;
}

export interface WorkflowIngressMatch {
  workflowId: number;
  matchIndex: number;
}

export interface WorkflowIngressInput {
  candidates: WorkflowIngressCandidate[];
  message: string;
  attachmentParts: ModelContentPart[];
  files: SlackFile[];
}

function parsedPlan(record: WorkflowRecord): WorkflowPlan {
  return workflowPlanSchema.parse(JSON.parse(record.plan_json));
}

function parsedCompletionPolicy(record: WorkflowRecord): WorkflowCompletionPolicy | null {
  if (!record.completion_policy_json) return null;
  const parsed: unknown = JSON.parse(record.completion_policy_json);
  return workflowCompletionPolicySchema.parse(parsed);
}

function trustedEventCount(record: WorkflowRecord, eventKind: string): number {
  try {
    const state = JSON.parse(record.state_json) as {
      trustedEventCounts?: Record<string, unknown>;
    };
    const count = state.trustedEventCounts?.[eventKind];
    return typeof count === "number" && Number.isInteger(count) && count >= 0 ? count : 0;
  } catch {
    return 0;
  }
}

function sameDestination(left: string, right: string): boolean {
  return (
    left.trim().normalize("NFKC").toLocaleLowerCase("en-US") ===
    right.trim().normalize("NFKC").toLocaleLowerCase("en-US")
  );
}

export function completionForWorkflow(
  record: WorkflowRecord,
): Omit<WorkflowCompletionProgress, "name" | "completed"> | null {
  const policy = parsedCompletionPolicy(record);
  if (!policy) return null;
  return {
    summary: policy.summary,
    current: trustedEventCount(record, policy.event),
    target: policy.target,
  };
}

export function planForWorkflow(record: WorkflowRecord): WorkflowPlan {
  return parsedPlan(record);
}

export function nodeForWorkflow(record: WorkflowRecord): WorkflowNode {
  const node = parsedPlan(record).nodes.find(
    (candidate) => candidate.id === record.current_node_id,
  );
  if (!node) throw new Error("The workflow's current node does not exist.");
  return node;
}

function nextRunForNode(node: WorkflowNode, now: number): number | null {
  if (node.type === "delay") return now + node.seconds * 1_000;
  if (node.type === "await") {
    return node.timeout_seconds === undefined ? null : now + node.timeout_seconds * 1_000;
  }
  return now;
}

export function ingressCandidates(records: WorkflowRecord[]): WorkflowIngressCandidate[] {
  return records.flatMap((record) => {
    const node = nodeForWorkflow(record);
    if (node.type !== "await" && node.type !== "repeat") return [];
    return [
      {
        workflowId: record.id,
        name: record.name,
        matches: node.matches.map((match, index) => ({
          index,
          condition: match.condition,
          evidence: match.evidence,
        })),
      },
    ];
  });
}

export class WorkflowRepository {
  private readonly listeners = new Set<() => void>();

  constructor(private readonly db: MattDatabase) {}

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void {
    for (const listener of this.listeners) listener();
  }

  create(input: {
    context: RuntimeContext;
    name: string;
    plan: WorkflowPlan;
    startsAt: number;
    expiresAt: number;
    delivery: ScheduleDelivery;
    cancelMessage?: string;
    completionPolicy?: WorkflowCompletionPolicy;
    now?: number;
  }): WorkflowRecord {
    const now = input.now ?? Date.now();
    const name = input.name.trim();
    const cancelMessage = input.cancelMessage?.trim() || null;
    const plan = workflowPlanSchema.parse(input.plan);
    const completionPolicy = input.completionPolicy
      ? workflowCompletionPolicySchema.parse(input.completionPolicy)
      : null;
    if (completionPolicy) {
      const completionNode = plan.nodes.find(
        (node) => node.id === completionPolicy.completion_node,
      );
      if (completionNode?.type !== "complete") {
        throw new Error("Workflow completion_policy must target a complete node.");
      }
    }
    const planJson = JSON.stringify(plan);
    const completionPolicyJson = completionPolicy ? JSON.stringify(completionPolicy) : null;
    if (name.length < 2 || name.length > 80)
      throw new Error("Workflow name must be 2–80 characters.");
    if (planJson.length > 30_000) throw new Error("Workflow plan is too large.");
    if (cancelMessage && cancelMessage.length > 1_000) {
      throw new Error("Workflow cancellation message must be at most 1,000 characters.");
    }
    if (input.startsAt < now - 1_000) throw new Error("Workflow start time cannot be in the past.");
    if (input.expiresAt <= input.startsAt) {
      throw new Error("Workflow expiration must be after its start time.");
    }
    if (input.expiresAt - input.startsAt > MAX_WORKFLOW_LIFETIME_MS) {
      throw new Error("A workflow may run for at most seven days.");
    }
    const active = this.db.raw
      .query<{ count: number }, [string, string]>(`
        SELECT count(*) AS count FROM workflows
        WHERE workspace_id = ? AND creator_user_id = ?
          AND status IN ('scheduled', 'active') AND deleted_at IS NULL
      `)
      .get(input.context.workspaceId, input.context.requesterId)?.count;
    if ((active ?? 0) >= 10) throw new Error("You already have 10 active workflows.");
    const result = this.db.raw
      .query(`
        INSERT INTO workflows(
          workspace_id, creator_user_id, channel_id, thread_ts, surface, delivery_mode,
          name, plan_json, current_node_id, node_entered_at, starts_at, next_run_at,
          expires_at, status, cancel_message, completion_policy_json, state_json,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        input.context.workspaceId,
        input.context.requesterId,
        input.context.channelId,
        input.context.threadTs,
        input.context.surface,
        input.delivery,
        name,
        planJson,
        plan.start_node,
        input.startsAt,
        input.startsAt,
        input.startsAt,
        input.expiresAt,
        input.startsAt > now ? "scheduled" : "active",
        cancelMessage,
        completionPolicyJson,
        JSON.stringify({ trustedEventCounts: {} }),
        now,
        now,
      );
    const workflow = this.get(Number(result.lastInsertRowid));
    if (!workflow) throw new Error("Workflow insert did not persist.");
    this.changed();
    return workflow;
  }

  get(id: number): WorkflowRecord | null {
    return (
      this.db.raw.query<WorkflowRecord, [number]>("SELECT * FROM workflows WHERE id = ?").get(id) ??
      null
    );
  }

  list(workspaceId: string, creatorUserId: string): WorkflowRecord[] {
    return this.db.raw
      .query<WorkflowRecord, [string, string]>(`
        SELECT * FROM workflows
        WHERE workspace_id = ? AND creator_user_id = ?
          AND status IN ('scheduled', 'active') AND deleted_at IS NULL
        ORDER BY COALESCE(next_run_at, expires_at), id
      `)
      .all(workspaceId, creatorUserId);
  }

  awaiting(workspaceId: string, creatorUserId: string, now = Date.now()): WorkflowRecord[] {
    this.expire(now);
    return this.list(workspaceId, creatorUserId).filter((record) => {
      if (record.status !== "active" || record.starts_at > now) return false;
      const node = nodeForWorkflow(record);
      return node.type === "await" || node.type === "repeat";
    });
  }

  leaseDue(now: number, leaseMs = 60_000, limit = 20): WorkflowRecord[] {
    this.expire(now);
    const token = randomUUID();
    const ids = this.db.raw.transaction(() => {
      const due = this.db.raw
        .query<{ id: number }, [number, number, number, number]>(`
          SELECT id FROM workflows
          WHERE status IN ('scheduled', 'active') AND next_run_at IS NOT NULL
            AND next_run_at <= ? AND expires_at > ?
            AND deleted_at IS NULL
            AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
          ORDER BY next_run_at, id LIMIT ?
        `)
        .all(now, now, now, limit)
        .map((record) => record.id);
      for (const id of due) {
        this.db.raw
          .query("UPDATE workflows SET lease_token = ?, lease_expires_at = ? WHERE id = ?")
          .run(token, now + leaseMs, id);
      }
      return due;
    })();
    if (!ids.length) return [];
    return this.db.raw
      .query<WorkflowRecord, [string]>(
        "SELECT * FROM workflows WHERE lease_token = ? ORDER BY next_run_at, id",
      )
      .all(token);
  }

  nextDueAt(now = Date.now()): number | null {
    this.expire(now);
    return (
      this.db.raw
        .query<{ next_run_at: number | null }, [number]>(`
          SELECT min(
            CASE
              WHEN lease_expires_at IS NOT NULL AND lease_expires_at > ?
                THEN max(next_run_at, lease_expires_at)
              ELSE next_run_at
            END
          ) AS next_run_at FROM workflows
          WHERE status IN ('scheduled', 'active') AND next_run_at IS NOT NULL
            AND deleted_at IS NULL
        `)
        .get(now)?.next_run_at ?? null
    );
  }

  enterNode(id: number, expectedNodeId: string, nextNodeId: string, now: number): boolean {
    const record = this.get(id);
    if (!record || record.current_node_id !== expectedNodeId) return false;
    const next = parsedPlan(record).nodes.find((node) => node.id === nextNodeId);
    if (!next) throw new Error("Workflow transition target does not exist.");
    const changed = this.db.raw
      .query(`
        UPDATE workflows SET current_node_id = ?, node_entered_at = ?, iteration = 0,
          next_run_at = ?, status = 'active', updated_at = ?, lease_token = NULL,
          lease_expires_at = NULL
        WHERE id = ? AND current_node_id = ? AND status IN ('scheduled', 'active')
          AND deleted_at IS NULL
      `)
      .run(nextNodeId, now, nextRunForNode(next, now), now, id, expectedNodeId).changes;
    if (changed) this.changed();
    return changed === 1;
  }

  markActive(id: number, now: number): void {
    this.db.raw
      .query(`
        UPDATE workflows SET status = 'active', updated_at = ?
        WHERE id = ? AND status = 'scheduled' AND starts_at <= ?
          AND deleted_at IS NULL
      `)
      .run(now, id, now);
  }

  repeatDelivered(
    record: WorkflowRecord,
    node: Extract<WorkflowNode, { type: "repeat" }>,
    now: number,
  ): void {
    const nextIteration = record.iteration + 1;
    const messageCount = record.message_count + 1;
    if (
      messageCount >= MAX_WORKFLOW_MESSAGES ||
      (node.max_occurrences !== undefined && nextIteration >= node.max_occurrences)
    ) {
      if (node.on_exhausted) {
        this.db.raw
          .query(`
            UPDATE workflows SET message_count = ?, iteration = ?, updated_at = ?,
              lease_token = NULL, lease_expires_at = NULL
            WHERE id = ? AND current_node_id = ? AND status = 'active'
          `)
          .run(messageCount, nextIteration, now, record.id, record.current_node_id);
        this.enterNode(record.id, record.current_node_id, node.on_exhausted, now);
      } else {
        this.finish(record.id, "completed", now);
      }
      return;
    }
    this.db.raw
      .query(`
        UPDATE workflows SET iteration = ?, message_count = ?, next_run_at = ?, updated_at = ?,
          lease_token = NULL, lease_expires_at = NULL
        WHERE id = ? AND current_node_id = ? AND status = 'active'
      `)
      .run(
        nextIteration,
        messageCount,
        now + node.interval_seconds * 1_000,
        now,
        record.id,
        record.current_node_id,
      );
    this.changed();
  }

  messageDelivered(record: WorkflowRecord, nextNodeId: string, now: number): void {
    this.db.raw
      .query(`
        UPDATE workflows SET message_count = message_count + 1, updated_at = ?,
          lease_token = NULL, lease_expires_at = NULL
        WHERE id = ? AND current_node_id = ? AND status IN ('scheduled', 'active')
      `)
      .run(now, record.id, record.current_node_id);
    this.enterNode(record.id, record.current_node_id, nextNodeId, now);
  }

  applyMatch(id: number, expectedNodeId: string, matchIndex: number, now: number): boolean {
    const record = this.get(id);
    if (!record || record.current_node_id !== expectedNodeId || record.status !== "active")
      return false;
    const node = nodeForWorkflow(record);
    if (node.type !== "await" && node.type !== "repeat") return false;
    const match = node.matches[matchIndex];
    if (!match) return false;
    return this.enterNode(id, expectedNodeId, match.next, now);
  }

  applyTimeout(record: WorkflowRecord, now: number): boolean {
    const node = nodeForWorkflow(record);
    if (node.type !== "await" || !node.on_timeout) return false;
    return this.enterNode(record.id, record.current_node_id, node.on_timeout, now);
  }

  recordTrustedEvent(input: {
    context: RuntimeContext;
    eventKind: "brain_list_item_removed";
    destinationTitle: string;
    eventKey: string;
    now?: number;
  }): WorkflowCompletionProgress[] {
    const now = input.now ?? Date.now();
    const progress = this.db.raw.transaction(() => {
      const records = this.db.raw
        .query<WorkflowRecord, [string, string, number]>(`
          SELECT * FROM workflows
          WHERE workspace_id = ? AND creator_user_id = ?
            AND status IN ('scheduled', 'active') AND starts_at <= ?
            AND completion_policy_json IS NOT NULL AND deleted_at IS NULL
          ORDER BY id
        `)
        .all(input.context.workspaceId, input.context.requesterId, now);
      const updates: WorkflowCompletionProgress[] = [];
      for (const record of records) {
        const policy = parsedCompletionPolicy(record);
        if (
          !policy ||
          policy.event !== input.eventKind ||
          !sameDestination(policy.destination_title, input.destinationTitle)
        ) {
          continue;
        }
        const inserted = this.db.raw
          .query(`
            INSERT OR IGNORE INTO workflow_events(workflow_id, event_key, event_kind, occurred_at)
            VALUES (?, ?, ?, ?)
          `)
          .run(record.id, input.eventKey, input.eventKind, now).changes;
        if (!inserted) continue;
        const current =
          this.db.raw
            .query<{ count: number }, [number, string]>(`
              SELECT count(*) AS count FROM workflow_events
              WHERE workflow_id = ? AND event_kind = ?
            `)
            .get(record.id, input.eventKind)?.count ?? 0;
        const stateJson = JSON.stringify({
          trustedEventCounts: { [input.eventKind]: current },
        });
        const completed = current >= policy.target;
        if (completed) {
          this.db.raw
            .query(`
              UPDATE workflows SET state_json = ?, current_node_id = ?, node_entered_at = ?,
                iteration = 0, next_run_at = ?, status = 'active', updated_at = ?,
                lease_token = NULL, lease_expires_at = NULL
              WHERE id = ? AND status IN ('scheduled', 'active') AND deleted_at IS NULL
            `)
            .run(stateJson, policy.completion_node, now, now, now, record.id);
        } else {
          this.db.raw
            .query("UPDATE workflows SET state_json = ?, updated_at = ? WHERE id = ?")
            .run(stateJson, now, record.id);
        }
        updates.push({
          name: record.name,
          summary: policy.summary,
          current,
          target: policy.target,
          completed,
        });
      }
      return updates;
    })();
    if (progress.length) this.changed();
    return progress;
  }

  cancel(
    id: number,
    workspaceId: string,
    creatorUserId: string,
    now = Date.now(),
  ): WorkflowRecord | null {
    const changed = this.db.raw
      .query(`
        UPDATE workflows SET status = 'cancelled', finished_at = ?, updated_at = ?,
          finished_reason = 'explicit_cancel', deleted_at = ?, next_run_at = NULL,
          lease_token = NULL, lease_expires_at = NULL
        WHERE id = ? AND workspace_id = ? AND creator_user_id = ?
          AND status IN ('scheduled', 'active')
      `)
      .run(now, now, now, id, workspaceId, creatorUserId).changes;
    if (!changed) return null;
    this.changed();
    return this.get(id);
  }

  finish(
    id: number,
    status: Extract<WorkflowStatus, "completed" | "expired" | "failed">,
    now: number,
    error?: string,
  ): void {
    const changed = this.db.raw
      .query(`
        UPDATE workflows SET status = ?, finished_at = ?, updated_at = ?, next_run_at = NULL,
          lease_token = NULL, lease_expires_at = NULL, last_error = ?,
          finished_reason = ?, deleted_at = ?
        WHERE id = ? AND status IN ('scheduled', 'active')
      `)
      .run(status, now, now, error?.slice(0, 500) ?? null, status, now, id).changes;
    if (changed) this.changed();
  }

  releaseLease(id: number): void {
    this.db.raw
      .query("UPDATE workflows SET lease_token = NULL, lease_expires_at = NULL WHERE id = ?")
      .run(id);
  }

  expire(now = Date.now()): number {
    const changed = this.db.raw
      .query(`
        UPDATE workflows SET status = 'expired', finished_at = ?, updated_at = ?,
          finished_reason = 'expired', deleted_at = ?, next_run_at = NULL,
          lease_token = NULL, lease_expires_at = NULL
        WHERE status IN ('scheduled', 'active') AND expires_at <= ? AND deleted_at IS NULL
      `)
      .run(now, now, now, now).changes;
    if (changed) this.changed();
    return changed;
  }
}
