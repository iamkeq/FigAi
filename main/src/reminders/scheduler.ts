import { createHash } from "node:crypto";
import { TurnCoordinator } from "../concurrency.ts";
import type { ReminderRecord, ReminderRepository } from "../db/reminders.ts";
import { errorMessage, log } from "../logger.ts";
import type { SlackClient } from "../slack/client.ts";
import {
  appendWriteReceiptFooter,
  removeInternalBrainLinks,
  splitSlackResponse,
} from "../slack/format.ts";
import type { Clock } from "../types.ts";
import { systemClock } from "../types.ts";
import { ScheduledTaskRunError, type ScheduledTaskRunner } from "./agent-runner.ts";
import { slackDeliveryTarget } from "./delivery.ts";

function clientMessageId(reminder: ReminderRecord, suffix = "reminder"): string {
  const hash = createHash("sha256")
    .update(
      suffix === "reminder"
        ? `${reminder.id}:${reminder.next_run_at}`
        : `${reminder.id}:${reminder.next_run_at}:${suffix}`,
    )
    .digest("hex");
  return `${hash.slice(0, 8)}-${hash.slice(8, 12)}-4${hash.slice(13, 16)}-a${hash.slice(17, 20)}-${hash.slice(20, 32)}`;
}

function standaloneScheduledResult(response: string): string {
  const original = response.trim();
  const cleaned = original
    .replace(/^(?:yes|sure|certainly)[.!,:;\s—-]+/i, "")
    .replace(/^as requested[.!,:;\s—-]+/i, "")
    .trim();
  return cleaned || original;
}

function notificationTitle(title: string): string {
  return title.replace(/[*_`<>[\]]/g, "").trim();
}

export class ReminderScheduler {
  private timer: ReturnType<typeof setInterval> | null = null;
  private polling = false;
  private readonly inFlight = new Map<string, Promise<void>>();

  constructor(
    private readonly reminders: ReminderRepository,
    private readonly slack: SlackClient,
    private readonly clock: Clock = systemClock,
    private readonly intervalMs = 15_000,
    private readonly sleep: (milliseconds: number) => Promise<void> = Bun.sleep,
    private readonly agentTasks?: ScheduledTaskRunner,
    private readonly turns: TurnCoordinator = new TurnCoordinator(2),
  ) {}

  start(): void {
    if (this.timer) return;
    void this.poll();
    this.timer = setInterval(() => void this.poll(), this.intervalMs);
  }

  async stop(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    await this.drain();
  }

  async drain(): Promise<void> {
    while (this.inFlight.size > 0) await Promise.allSettled(this.inFlight.values());
  }

  async poll(): Promise<void> {
    if (this.polling) return;
    this.polling = true;
    try {
      const now = this.clock.now().getTime();
      const due = this.reminders.leaseDue(now, 15 * 60_000);
      for (const reminder of due.filter((row) => row.kind === "reminder")) {
        await this.deliverReminder(reminder, now);
      }
      for (const task of due.filter((row) => row.kind === "agent_task")) {
        this.dispatchAgentTask(task, now);
      }
    } finally {
      this.polling = false;
    }
  }

  private dispatchAgentTask(task: ReminderRecord, now: number): void {
    const key = `${task.id}:${task.next_run_at}`;
    if (this.inFlight.has(key)) return;
    const work = this.turns
      .run(task.channel_id, task.thread_ts, () => this.deliverAgentTask(task, now))
      .catch((error) => {
        log("error", "scheduled_agent_dispatch_failed", {
          taskId: task.id,
          error: errorMessage(error),
        });
      })
      .finally(() => this.inFlight.delete(key));
    this.inFlight.set(key, work);
  }

  private async deliverReminder(reminder: ReminderRecord, now: number): Promise<void> {
    const late = now - reminder.next_run_at > this.intervalMs;
    const target = slackDeliveryTarget(reminder);
    const prefix = target.mentionCreator ? `<@${reminder.creator_user_id}> ` : "";
    const marker = late && reminder.recurrence === "once" ? "_(late reminder)_ " : "";
    const recurringTitle =
      reminder.recurrence !== "once" && reminder.notification_title
        ? `*${notificationTitle(reminder.notification_title)}*\n`
        : "";
    const text = `${prefix}${marker}⏰ ${recurringTitle}${reminder.text}`;
    let lastError = "unknown Slack error";
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await this.slack.chat.postMessage({
          channel: target.channel,
          ...(target.threadTs ? { thread_ts: target.threadTs } : {}),
          text,
          client_msg_id: clientMessageId(reminder),
        });
        this.reminders.markDelivered(reminder, this.clock.now().getTime(), late, attempt);
        return;
      } catch (error) {
        lastError = errorMessage(error);
        if (attempt < 3) await this.sleep(attempt * 500);
      }
    }
    this.reminders.markAttemptFailure(reminder, lastError, this.clock.now().getTime(), true, 3);
    log("error", "reminder_delivery_failed", { reminderId: reminder.id, error: lastError });
  }

  private async deliverAgentTask(task: ReminderRecord, now: number): Promise<void> {
    const late = now - task.next_run_at > this.intervalMs;
    const claim = this.reminders.claimAgentRun(task, this.clock.now().getTime());
    if (
      claim.state === "ready" &&
      (claim.run.response_text !== null || claim.run.suppress_delivery === 1)
    ) {
      await this.deliverAgentResult(
        task,
        claim.run.response_text ?? "",
        late,
        claim.run.suppress_delivery === 1,
      );
      return;
    }
    if (claim.state !== "started") {
      const reason =
        claim.state === "running"
          ? "MattGPT restarted while this scheduled task was running, so it was not replayed."
          : "This scheduled task could not be safely resumed.";
      this.reminders.markAgentRunFailed(
        task,
        reason,
        claim.run.write_performed === 1,
        this.clock.now().getTime(),
      );
      this.reminders.markOccurrenceFailed(task, reason, this.clock.now().getTime());
      await this.postFailure(task, reason);
      return;
    }
    if (!this.agentTasks) {
      const reason = "Scheduled agent execution is unavailable.";
      this.reminders.markAgentRunFailed(task, reason, false, this.clock.now().getTime());
      this.reminders.markOccurrenceFailed(task, reason, this.clock.now().getTime());
      await this.postFailure(task, reason);
      return;
    }

    try {
      const result = await this.agentTasks.run(task);
      this.reminders.markAgentReady(
        task,
        result.text,
        result.writePerformed,
        this.clock.now().getTime(),
        result.suppressDelivery === true,
      );
      await this.deliverAgentResult(task, result.text, late, result.suppressDelivery === true);
    } catch (error) {
      const failure =
        error instanceof ScheduledTaskRunError
          ? error
          : new ScheduledTaskRunError(error, [], 1, false);
      const reason = errorMessage(failure);
      this.reminders.markAgentRunFailed(
        task,
        reason,
        failure.writePerformed,
        this.clock.now().getTime(),
      );
      if (failure.authorizationDenied) {
        this.reminders.markOccurrenceFailed(task, reason, this.clock.now().getTime(), 1);
        this.reminders.disable(task, reason, this.clock.now().getTime());
      } else {
        this.reminders.markOccurrenceFailed(
          task,
          reason,
          this.clock.now().getTime(),
          Math.max(failure.attempts, 1),
        );
      }
      await this.postFailure(task, reason, failure.writeReceipts);
      log("error", "scheduled_agent_task_failed", { taskId: task.id, error: reason });
    }
  }

  private async deliverAgentResult(
    task: ReminderRecord,
    response: string,
    late: boolean,
    suppressDelivery = false,
  ): Promise<void> {
    if (suppressDelivery) {
      this.reminders.markDelivered(task, this.clock.now().getTime(), late, 0);
      return;
    }
    const target = slackDeliveryTarget(task);
    const prefix = target.mentionCreator ? `<@${task.creator_user_id}> ` : "";
    const lateMarker = late && task.recurrence === "once" ? "_(late)_ " : "";
    const title =
      task.recurrence !== "once" && task.notification_title
        ? `*${notificationTitle(task.notification_title)}*\n`
        : "";
    const result = standaloneScheduledResult(response);
    const chunks = splitSlackResponse(
      removeInternalBrainLinks(`${prefix}${lateMarker}${title}${result}`),
    );
    let lastError = "unknown Slack error";
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        for (const [index, chunk] of chunks.entries()) {
          await this.slack.chat.postMessage({
            channel: target.channel,
            ...(target.threadTs ? { thread_ts: target.threadTs } : {}),
            text: chunk,
            blocks: [{ type: "markdown", text: chunk }],
            unfurl_links: false,
            unfurl_media: false,
            client_msg_id: clientMessageId(task, `result:${index}`),
          });
        }
        this.reminders.markDelivered(task, this.clock.now().getTime(), late, attempt);
        return;
      } catch (error) {
        lastError = errorMessage(error);
        if (attempt < 3) await this.sleep(attempt * 500);
      }
    }
    this.reminders.markAgentRunFailed(task, lastError, false, this.clock.now().getTime());
    this.reminders.markOccurrenceFailed(task, lastError, this.clock.now().getTime(), 3);
    log("error", "scheduled_agent_result_delivery_failed", { taskId: task.id, error: lastError });
  }

  private async postFailure(
    task: ReminderRecord,
    detail: string,
    writeReceipts: string[] = [],
  ): Promise<void> {
    const target = slackDeliveryTarget(task);
    const prefix = target.mentionCreator ? `<@${task.creator_user_id}> ` : "";
    const action =
      writeReceipts.length > 0
        ? appendWriteReceiptFooter(
            "I hit an internal error after completing the actions below. I did not replay them.",
            writeReceipts,
          )
        : detail.includes("no longer authorized")
          ? "This scheduled task was disabled because its creator or destination is no longer authorized."
          : "I couldn’t complete this scheduled task. I did not replay any completed actions.";
    await this.slack.chat
      .postMessage({
        channel: target.channel,
        ...(target.threadTs ? { thread_ts: target.threadTs } : {}),
        text: `${prefix}${action}`,
        blocks: [{ type: "markdown", text: `${prefix}${action}` }],
        unfurl_links: false,
        unfurl_media: false,
        client_msg_id: clientMessageId(task, "failure"),
      })
      .catch(() => {});
  }
}
