import { createHash } from "node:crypto";
import { KeyedMutex } from "../concurrency.ts";
import {
  MAX_WORKFLOW_MESSAGES,
  nodeForWorkflow,
  type WorkflowIngressMatch,
  type WorkflowRecord,
  type WorkflowRepository,
} from "../db/workflows.ts";
import { errorMessage, log } from "../logger.ts";
import { slackDeliveryTarget } from "../reminders/delivery.ts";
import type { SlackClient } from "../slack/client.ts";

function clientMessageId(workflow: WorkflowRecord, suffix: string): string {
  const hash = createHash("sha256")
    .update(`${workflow.id}:${workflow.current_node_id}:${workflow.iteration}:${suffix}`)
    .digest("hex");
  return `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-a${hash.slice(17, 20)}-${hash.slice(20, 32)}`;
}

export interface WorkflowIngressOutcome {
  consumed: boolean;
  matched: number;
  cancelled: number;
}

export class WorkflowEngine {
  private readonly mutex = new KeyedMutex();

  constructor(
    private readonly workflows: WorkflowRepository,
    private readonly slack: SlackClient,
    private readonly sleep: (milliseconds: number) => Promise<void> = Bun.sleep,
  ) {}

  async handleDue(id: number, now = Date.now()): Promise<void> {
    await this.mutex.run(String(id), () => this.drain(id, now));
  }

  async applyIngress(input: {
    matches: WorkflowIngressMatch[];
    cancelIds: number[];
    workspaceId: string;
    creatorUserId: string;
    now?: number;
  }): Promise<WorkflowIngressOutcome> {
    const now = input.now ?? Date.now();
    let matched = 0;
    let cancelled = 0;
    const matchByWorkflow = new Map<number, number>();
    for (const match of input.matches) {
      if (!matchByWorkflow.has(match.workflowId)) {
        matchByWorkflow.set(match.workflowId, match.matchIndex);
      }
    }
    const cancelIds = new Set(input.cancelIds);
    for (const id of new Set([...cancelIds, ...matchByWorkflow.keys()])) {
      await this.mutex.run(String(id), async () => {
        const record = this.workflows.get(id);
        if (
          !record ||
          record.workspace_id !== input.workspaceId ||
          record.creator_user_id !== input.creatorUserId ||
          !["scheduled", "active"].includes(record.status)
        ) {
          return;
        }
        if (cancelIds.has(id)) {
          const cancelledRecord = this.workflows.cancel(
            id,
            input.workspaceId,
            input.creatorUserId,
            now,
          );
          if (!cancelledRecord) return;
          cancelled += 1;
          if (cancelledRecord.cancel_message) {
            await this.post(cancelledRecord, cancelledRecord.cancel_message, "cancel");
          }
          return;
        }
        const matchIndex = matchByWorkflow.get(id);
        if (matchIndex === undefined) return;
        if (!this.workflows.applyMatch(id, record.current_node_id, matchIndex, now)) return;
        matched += 1;
        await this.drainUnlocked(id, now);
      });
    }
    return { consumed: matched + cancelled > 0, matched, cancelled };
  }

  private drain(id: number, now: number): Promise<void> {
    return this.drainUnlocked(id, now);
  }

  private async drainUnlocked(id: number, now: number): Promise<void> {
    for (let step = 0; step < 40; step += 1) {
      const record = this.workflows.get(id);
      if (!record || !["scheduled", "active"].includes(record.status)) return;
      if (record.expires_at <= now) {
        this.workflows.finish(id, "expired", now);
        return;
      }
      if (record.next_run_at === null || record.next_run_at > now) {
        this.workflows.releaseLease(id);
        return;
      }
      this.workflows.markActive(id, now);
      const current = this.workflows.get(id);
      if (!current) return;
      const node = nodeForWorkflow(current);
      try {
        if (node.type === "message") {
          if (current.message_count >= MAX_WORKFLOW_MESSAGES) {
            this.workflows.finish(id, "failed", now, "Workflow reached its message safety cap.");
            return;
          }
          await this.post(current, node.text, "message");
          this.workflows.messageDelivered(current, node.next, now);
          continue;
        }
        if (node.type === "delay") {
          this.workflows.enterNode(current.id, current.current_node_id, node.next, now);
          continue;
        }
        if (node.type === "await") {
          if (node.on_timeout && this.workflows.applyTimeout(current, now)) continue;
          this.workflows.releaseLease(id);
          return;
        }
        if (node.type === "repeat") {
          if (current.message_count >= MAX_WORKFLOW_MESSAGES) {
            this.workflows.finish(id, "failed", now, "Workflow reached its message safety cap.");
            return;
          }
          const message =
            node.messages[current.iteration % node.messages.length] ?? node.messages[0];
          if (!message) throw new Error("Repeat workflow has no message.");
          await this.post(current, message, "repeat");
          this.workflows.repeatDelivered(current, node, now);
          return;
        }
        if (node.message) await this.post(current, node.message, "complete");
        this.workflows.finish(id, "completed", now);
        return;
      } catch (error) {
        this.workflows.finish(id, "failed", now, errorMessage(error));
        log("error", "workflow_execution_failed", { workflowId: id, error: errorMessage(error) });
        return;
      }
    }
    this.workflows.finish(id, "failed", now, "Workflow exceeded the immediate transition limit.");
  }

  private async post(workflow: WorkflowRecord, text: string, suffix: string): Promise<void> {
    const target = slackDeliveryTarget(workflow);
    const prefix = target.mentionCreator ? `<@${workflow.creator_user_id}> ` : "";
    const body = `${prefix}${text}`;
    let lastError: unknown = new Error("Unknown Slack delivery failure.");
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await this.slack.chat.postMessage({
          channel: target.channel,
          ...(target.threadTs ? { thread_ts: target.threadTs } : {}),
          text: body,
          blocks: [{ type: "markdown", text: body }],
          unfurl_links: false,
          unfurl_media: false,
          client_msg_id: clientMessageId(workflow, suffix),
        });
        return;
      } catch (error) {
        lastError = error;
        if (attempt < 3) await this.sleep(attempt * 500);
      }
    }
    throw lastError;
  }
}
