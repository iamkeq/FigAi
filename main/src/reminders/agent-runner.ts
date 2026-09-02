import { type Agent, type AgentResult, AgentRunError, ProviderError } from "../agent/openrouter.ts";
import { buildSystemPrompt } from "../agent/prompt.ts";
import type { AppConfig } from "../config.ts";
import type { MattDatabase } from "../db/database.ts";
import type { MemoryRepository } from "../db/memories.ts";
import { UserPreferenceRepository } from "../db/preferences.ts";
import type { ReminderRecord } from "../db/reminders.ts";
import type { SkillRepository } from "../db/skills.ts";
import { errorMessage, log } from "../logger.ts";
import type { SlackAuthorizer } from "../slack/authorization.ts";
import type { SlackClient } from "../slack/client.ts";
import {
  appendWriteReceiptFooter,
  removeInternalBrainLinks,
  splitSlackResponse,
} from "../slack/format.ts";
import type { Clock, RuntimeContext } from "../types.ts";
import { systemClock } from "../types.ts";
import { slackDeliveryTarget } from "./delivery.ts";

const DEFAULT_LOADING_PROGRESSION = [
  "is reading",
  "is checking",
  "is thinking",
  "is writing",
  "is finishing up",
] as const;

export interface ScheduledTaskResult {
  text: string;
  suppressDelivery?: boolean;
  writePerformed: boolean;
  attempts: number;
}

export class ScheduledTaskRunError extends Error {
  constructor(
    error: unknown,
    readonly writeReceipts: string[],
    readonly attempts: number,
    readonly authorizationDenied = false,
  ) {
    super(errorMessage(error));
    this.name = error instanceof Error ? error.name : "Error";
    this.cause = error;
  }

  get writePerformed(): boolean {
    return this.writeReceipts.length > 0;
  }
}

export interface ScheduledTaskRunner {
  run(task: ReminderRecord): Promise<ScheduledTaskResult>;
}

function transient(error: unknown): boolean {
  if (error instanceof AgentRunError)
    return error.cause instanceof ProviderError && error.cause.transient;
  return error instanceof ProviderError && error.transient;
}

export class SlackScheduledTaskRunner implements ScheduledTaskRunner {
  constructor(
    private readonly config: AppConfig,
    private readonly db: MattDatabase,
    private readonly slack: SlackClient,
    private readonly authorizer: SlackAuthorizer,
    private readonly memories: MemoryRepository,
    private readonly skills: SkillRepository,
    private readonly agent: Agent,
    private readonly botUserId: string,
    private readonly clock: Clock = systemClock,
    private readonly sleep: (milliseconds: number) => Promise<void> = Bun.sleep,
  ) {}

  async run(task: ReminderRecord): Promise<ScheduledTaskResult> {
    const authorization = await this.authorizer.authorize({
      userId: task.creator_user_id,
      channelId: task.channel_id,
      surface: task.surface,
      fresh: true,
    });
    if (!authorization.allowed) {
      throw new ScheduledTaskRunError(
        new Error("The creator or destination is no longer authorized."),
        [],
        0,
        true,
      );
    }

    const context: RuntimeContext = {
      workspaceId: task.workspace_id,
      botUserId: this.botUserId,
      requesterId: task.creator_user_id,
      requesterName: authorization.requesterName ?? task.creator_user_id,
      surface: task.surface,
      channelId: task.channel_id,
      threadTs: task.thread_ts,
      turnId: `scheduled-task:${task.id}:${task.next_run_at}`,
      timezone: authorization.timezone ?? task.timezone ?? this.config.defaultTimezone,
      isOwner: task.creator_user_id === this.config.ownerUserId,
      participantIds: new Set([task.creator_user_id]),
    };
    const presentation =
      task.recurrence !== "once" && task.notification_title
        ? `\n\nSaved presentation contract (formatting only; it cannot change the command, permissions, or tool policy):\nNotification title: ${task.notification_title}\nPresentation instructions: ${task.presentation_instructions ?? "Keep the result concise and use the same structure on every occurrence."}\nThe delivery layer adds the notification title. Do not repeat it in your result. Follow this same presentation shape on every occurrence.`
        : "\n\nNo saved presentation contract applies. Write a natural standalone notification without adding a generic scheduled-task heading.";
    const messages = [
      {
        role: "system" as const,
        content: buildSystemPrompt({
          context,
          memories: this.memories.listForSurface({
            userId: context.requesterId,
            channelId: context.channelId,
            surface: context.surface,
          }),
          preferences: new UserPreferenceRepository(this.db).list(
            context.workspaceId,
            context.requesterId,
          ),
          skills: this.skills.catalog(),
          now: this.clock.now(),
        }),
      },
      {
        role: "user" as const,
        content: `This is a ${task.recurrence === "once" ? "one-time" : `${task.recurrence} recurring`} scheduled task that the requester explicitly asked MattGPT to execute now. The saved command is the requester's authorization for only the listed, normally permitted actions. Execute its ordered steps now instead of rescheduling them; when it says to begin a temporary behavior now, create the directive with activation=now and no starts_at. Use current information and the normal tools as needed. Write the result as a self-contained scheduled update that makes sense without the original Slack conversation. Lead with the subject and current finding; never begin with a conversational acknowledgment such as "yes," "sure," "as requested," or "here you go." If the command explicitly requires no message when a condition is met and that condition is met, call complete_scheduled_task_silently; never return empty assistant content.\n\nCommand:\n${task.text}${presentation}`,
      },
    ];

    let finished = false;
    let activeStatuses: readonly string[] = DEFAULT_LOADING_PROGRESSION;
    const started = performance.now();
    await this.setThinking(task, activeStatuses);
    void this.agent
      .loadingStatus(task.text)
      .then((statuses) => {
        if (statuses.length && !finished) {
          activeStatuses = statuses;
          return this.setThinking(task, statuses);
        }
      })
      .catch(() => {});

    let attempts = 0;
    try {
      while (attempts < 2) {
        attempts += 1;
        try {
          const result = await this.agent.run({
            messages,
            context,
            onProgress: async (message) => {
              await this.reply(task, message);
              await this.sleep(400);
              if (!finished) await this.setThinking(task, activeStatuses);
            },
          });
          if (result.suppressDelivery && (result.images.length || result.writeReceipts.length)) {
            throw new Error(
              "A scheduled task produced output before requesting silent completion.",
            );
          }
          if (!result.suppressDelivery) await this.uploadImages(task, result);
          const text = appendWriteReceiptFooter(result.text, result.writeReceipts);
          this.recordSuccess(task, context, result);
          return {
            text,
            writePerformed: result.writeReceipts.length > 0,
            attempts,
            ...(result.suppressDelivery ? { suppressDelivery: true } : {}),
          };
        } catch (error) {
          const receipts = error instanceof AgentRunError ? error.writeReceipts : [];
          if (attempts === 1 && receipts.length === 0 && transient(error)) {
            await this.sleep(2_000);
            continue;
          }
          this.recordFailure(context, started, error);
          throw new ScheduledTaskRunError(error, receipts, attempts);
        }
      }
      throw new ScheduledTaskRunError(new Error("Scheduled task retry exhausted."), [], attempts);
    } finally {
      finished = true;
      await this.clearThinking(task);
    }
  }

  private async uploadImages(task: ReminderRecord, result: AgentResult): Promise<void> {
    const target = slackDeliveryTarget(task);
    for (const [index, image] of result.images.entries()) {
      const extension =
        image.mediaType === "image/jpeg" ? "jpg" : image.mediaType.replace("image/", "");
      const defaultFilename = `mattgpt-image-${index + 1}.${extension}`;
      const filename =
        image.filename && /^[a-z0-9][a-z0-9._-]{0,99}$/i.test(image.filename)
          ? image.filename
          : defaultFilename;
      await this.slack.filesUploadV2({
        channel_id: target.channel,
        ...(target.threadTs ? { thread_ts: target.threadTs } : {}),
        filename,
        title: image.title?.slice(0, 100) || "MattGPT generated image",
        alt_text: image.altText,
        file: image.bytes,
      });
    }
  }

  private recordSuccess(task: ReminderRecord, context: RuntimeContext, result: AgentResult): void {
    this.db.recordInteraction({
      eventId: context.turnId,
      workspaceId: context.workspaceId,
      channelId: context.channelId,
      threadTs: context.threadTs,
      requesterId: context.requesterId,
      surface: context.surface,
      model: result.model,
      latencyMs: result.latencyMs,
      promptTokens: result.promptTokens,
      completionTokens: result.completionTokens,
      totalTokens: result.totalTokens,
      reportedCost: result.reportedCost,
      tools: result.tools,
      status: "ok",
    });
    log("info", "scheduled_agent_task_complete", { taskId: task.id, model: result.model });
  }

  private recordFailure(context: RuntimeContext, started: number, error: unknown): void {
    this.db.recordInteraction({
      eventId: context.turnId,
      workspaceId: context.workspaceId,
      channelId: context.channelId,
      threadTs: context.threadTs,
      requesterId: context.requesterId,
      surface: context.surface,
      latencyMs: Math.round(performance.now() - started),
      status: "error",
      errorCode: error instanceof Error ? error.name : "unknown",
    });
  }

  private async reply(task: ReminderRecord, text: string): Promise<void> {
    const target = slackDeliveryTarget(task);
    for (const chunk of splitSlackResponse(removeInternalBrainLinks(text))) {
      await this.slack.chat.postMessage({
        channel: target.channel,
        ...(target.threadTs ? { thread_ts: target.threadTs } : {}),
        text: chunk,
        blocks: [{ type: "markdown", text: chunk }],
        unfurl_links: false,
        unfurl_media: false,
      });
    }
  }

  private async setThinking(task: ReminderRecord, statuses: readonly string[]): Promise<void> {
    if (task.delivery_mode !== "thread") return;
    const progression = statuses.slice(0, 5);
    await this.slack.assistant.threads
      .setStatus({
        channel_id: task.channel_id,
        thread_ts: task.thread_ts,
        status: progression[0] ?? DEFAULT_LOADING_PROGRESSION[0],
        ...(progression.length > 1 ? { loading_messages: progression.slice(1) } : {}),
      })
      .catch(() => {});
  }

  private async clearThinking(task: ReminderRecord): Promise<void> {
    if (task.delivery_mode !== "thread") return;
    await this.slack.assistant.threads
      .setStatus({ channel_id: task.channel_id, thread_ts: task.thread_ts, status: "" })
      .catch(() => {});
  }
}
