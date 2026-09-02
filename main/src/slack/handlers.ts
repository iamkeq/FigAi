import { type Agent, AgentRunError, threadToChatMessages } from "../agent/openrouter.ts";
import { buildSystemPrompt } from "../agent/prompt.ts";
import { TurnCoordinator } from "../concurrency.ts";
import type { AppConfig } from "../config.ts";
import type { MattDatabase } from "../db/database.ts";
import {
  DIRECTIVE_POLICY_VERSION,
  requiresSuppressedDelivery,
  type TemporaryDirectiveRecord,
  TemporaryDirectiveRepository,
} from "../db/directives.ts";
import type { MemoryRepository } from "../db/memories.ts";
import { UserPreferenceRepository } from "../db/preferences.ts";
import type { SkillRepository } from "../db/skills.ts";
import {
  ingressCandidates,
  type WorkflowRecord,
  type WorkflowRepository,
} from "../db/workflows.ts";
import { type AttachmentManager, type PreparedAttachments, selectAttachments } from "../files.ts";
import { errorMessage, log } from "../logger.ts";
import type { RuntimeContext, SlackFile, ThreadMessage } from "../types.ts";
import type { WorkflowEngine } from "../workflows/engine.ts";
import type { SlackAuthorizer } from "./authorization.ts";
import type { SlackClient } from "./client.ts";
import type { SlashCommands } from "./commands.ts";
import {
  appendWriteReceiptFooter,
  removeInternalBrainLinks,
  splitSlackResponse,
} from "./format.ts";

const DEFAULT_LOADING_PROGRESSION = [
  "is reading",
  "is checking",
  "is thinking",
  "is writing",
  "is finishing up",
] as const;

export interface SlackEventInput {
  eventId: string;
  workspaceId: string;
  kind: "app_mention" | "message_im" | "message_channel";
  channelId: string;
  userId: string;
  ts: string;
  threadTs?: string;
  channelType?: string;
  text: string;
  subtype?: string;
  botId?: string;
  files?: SlackFile[];
}

export interface SlashInput {
  workspaceId: string;
  channelId: string;
  userId: string;
  text: string;
}

function slackTimestampMs(value: string): number {
  const seconds = Number.parseFloat(value);
  return Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds * 1_000) : Date.now();
}

function mergeDirectives(...groups: TemporaryDirectiveRecord[][]): TemporaryDirectiveRecord[] {
  const records = new Map<number, TemporaryDirectiveRecord>();
  for (const group of groups) {
    for (const directive of group) records.set(directive.id, directive);
  }
  return [...records.values()];
}

export class SlackHandlers {
  constructor(
    private readonly config: AppConfig,
    private readonly db: MattDatabase,
    private readonly slack: SlackClient,
    private readonly authorizer: SlackAuthorizer,
    private readonly memories: MemoryRepository,
    private readonly skills: SkillRepository,
    private readonly attachments: AttachmentManager,
    private readonly agent: Agent,
    private readonly commands: SlashCommands,
    private readonly botUserId: string,
    private readonly turns: TurnCoordinator = new TurnCoordinator(2),
    private readonly preferences: UserPreferenceRepository = new UserPreferenceRepository(db),
    private readonly directives: TemporaryDirectiveRepository = new TemporaryDirectiveRepository(
      db,
    ),
    private readonly workflows: WorkflowRepository | null = null,
    private readonly workflowEngine: WorkflowEngine | null = null,
  ) {}

  async handleEvent(event: SlackEventInput): Promise<void> {
    if (event.workspaceId !== this.workspaceId()) return;
    if (event.botId || event.userId === this.botUserId) return;
    if (event.subtype && !["file_share", "thread_broadcast"].includes(event.subtype)) return;
    if (event.kind === "message_channel" && event.text.includes(`<@${this.botUserId}>`)) return;
    const surface = event.kind === "message_im" ? "dm" : "channel";
    if (surface === "dm" && event.channelType !== "im") return;
    if (surface === "channel" && !this.config.allowedChannelIds.has(event.channelId)) return;
    const eventAt = slackTimestampMs(event.ts);
    const passiveWorkflowCandidates =
      event.kind === "message_channel"
        ? (this.workflows?.awaiting(event.workspaceId, event.userId, eventAt) ?? [])
        : undefined;
    if (passiveWorkflowCandidates && passiveWorkflowCandidates.length === 0) return;
    if (!this.db.claimEvent(event.eventId)) return;

    const authorization = await this.authorizer.authorize({
      userId: event.userId,
      channelId: event.channelId,
      surface,
    });
    if (!authorization.allowed) {
      if (surface === "dm") {
        await this.slack.chat.postMessage({
          channel: event.channelId,
          thread_ts: event.threadTs ?? event.ts,
          text: "MattGPT is limited to internal members of an approved channel.",
        });
      }
      return;
    }

    const threadTs = event.threadTs ?? event.ts;
    const context: RuntimeContext = {
      workspaceId: event.workspaceId,
      botUserId: this.botUserId,
      requesterId: event.userId,
      requesterName: authorization.requesterName ?? event.userId,
      surface,
      channelId: event.channelId,
      threadTs,
      turnId: event.eventId,
      timezone: authorization.timezone ?? this.config.defaultTimezone,
      isOwner: event.userId === this.config.ownerUserId,
    };
    if (event.kind === "message_channel") {
      await this.handleWorkflowIngress(event, context, eventAt, passiveWorkflowCandidates);
      return;
    }
    let eventDirectives = this.directives.activeFor(context, eventAt);
    for (const directive of eventDirectives) {
      if (directive.policy_version === DIRECTIVE_POLICY_VERSION) continue;
      try {
        const policy = await this.agent.compileDirectivePolicy({
          instruction: directive.directive_text,
          releaseCondition: directive.release_phrase,
        });
        const upgraded = this.directives.setPolicy({
          id: directive.id,
          workspaceId: context.workspaceId,
          userId: context.requesterId,
          policy,
        });
        if (upgraded) {
          eventDirectives = eventDirectives.map((record) =>
            record.id === upgraded.id ? upgraded : record,
          );
        }
      } catch (error) {
        log("warn", "directive_policy_upgrade_failed", { error: errorMessage(error) });
      }
    }
    let releasedDirectives: TemporaryDirectiveRecord[] = [];
    let bypassedDirectiveIds = new Set<number>();
    if (eventDirectives.length) {
      const ingress = await this.agent.evaluateDirectiveIngress(
        eventDirectives,
        event.text,
        context,
      );
      releasedDirectives = this.directives.completeAndReturn(
        ingress.satisfiedIds,
        context,
        eventAt,
      );
      bypassedDirectiveIds = new Set(ingress.bypassIds);
    }
    const releasedIds = new Set(releasedDirectives.map((directive) => directive.id));
    eventDirectives = eventDirectives.filter(
      (directive) => !releasedIds.has(directive.id) && !bypassedDirectiveIds.has(directive.id),
    );
    if (await this.handleWorkflowIngress(event, context, eventAt)) return;
    await this.turns.run(event.channelId, threadTs, () =>
      this.processTurn(
        event,
        context,
        releasedDirectives,
        eventDirectives,
        eventAt,
        bypassedDirectiveIds,
      ),
    );
  }

  private async handleWorkflowIngress(
    event: SlackEventInput,
    context: RuntimeContext,
    eventAt: number,
    knownAwaiting?: WorkflowRecord[],
  ): Promise<boolean> {
    if (!this.workflows || !this.workflowEngine) return false;
    const awaiting =
      knownAwaiting ?? this.workflows.awaiting(context.workspaceId, context.requesterId, eventAt);
    if (!awaiting.length) return false;
    let workflowAttachments: PreparedAttachments | null = null;
    try {
      workflowAttachments = event.files?.length
        ? await this.attachments.prepare(event.files)
        : { parts: [], notices: [], cleanup: () => {} };
      const ingress = await this.agent.evaluateWorkflowIngress(
        {
          candidates: ingressCandidates(awaiting),
          message: event.text,
          attachmentParts: workflowAttachments.parts,
          files: event.files ?? [],
        },
        context,
      );
      const outcome = await this.workflowEngine.applyIngress({
        matches: ingress.matches,
        cancelIds: ingress.cancelIds,
        workspaceId: context.workspaceId,
        creatorUserId: context.requesterId,
        now: eventAt,
      });
      if (!outcome.consumed) return false;
      this.db.recordInteraction({
        eventId: event.eventId,
        workspaceId: context.workspaceId,
        channelId: context.channelId,
        threadTs: context.threadTs,
        requesterId: context.requesterId,
        surface: context.surface,
        tools: ["workflow_ingress"],
        status: "ok",
      });
      return true;
    } catch (error) {
      log("warn", "workflow_ingress_failed", { error: errorMessage(error) });
      return false;
    } finally {
      workflowAttachments?.cleanup();
    }
  }

  async handleSlash(input: SlashInput): Promise<string> {
    if (input.workspaceId !== this.workspaceId())
      return "This MattGPT installation belongs to another workspace.";
    const surface = input.channelId.startsWith("D") ? "dm" : "channel";
    const authorization = await this.authorizer.authorize({
      userId: input.userId,
      channelId: input.channelId,
      surface,
    });
    if (!authorization.allowed) return "You are not authorized to use MattGPT here.";
    const context: RuntimeContext = {
      workspaceId: input.workspaceId,
      botUserId: this.botUserId,
      requesterId: input.userId,
      requesterName: authorization.requesterName ?? input.userId,
      surface,
      channelId: input.channelId,
      threadTs: "slash-command",
      turnId: "slash-command",
      timezone: authorization.timezone ?? this.config.defaultTimezone,
      isOwner: input.userId === this.config.ownerUserId,
    };
    try {
      return await this.commands.execute(input.text, context);
    } catch (error) {
      return errorMessage(error);
    }
  }

  private workspaceId(): string {
    return this.authorizer.workspaceId;
  }

  private async processTurn(
    event: SlackEventInput,
    context: RuntimeContext,
    releasedDirectives: TemporaryDirectiveRecord[] = [],
    eventDirectives: TemporaryDirectiveRecord[] = [],
    eventAt = Date.now(),
    bypassedDirectiveIds: ReadonlySet<number> = new Set(),
  ): Promise<void> {
    let prepared: PreparedAttachments | null = null;
    let finished = false;
    let activeStatuses: readonly string[] = DEFAULT_LOADING_PROGRESSION;
    let progressStatusRestoredAt: number | null = null;
    let completedWriteReceipts: string[] = [];
    let applicableForTurn = eventDirectives;
    const started = performance.now();
    try {
      const currentDirectives = (): TemporaryDirectiveRecord[] =>
        mergeDirectives(
          eventDirectives,
          this.directives
            .activeFor(context)
            .filter(
              (directive) =>
                directive.created_at <= eventAt && !bypassedDirectiveIds.has(directive.id),
            ),
        );
      applicableForTurn = currentDirectives();
      if (requiresSuppressedDelivery(applicableForTurn)) {
        const result = await this.agent.run({
          messages: [{ role: "user", content: `[${context.requesterName}] ${event.text}` }],
          context,
          directives: applicableForTurn,
          currentDirectives,
        });
        this.db.recordInteraction({
          eventId: event.eventId,
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
        return;
      }
      await this.setThinking(event.channelId, context.threadTs);
      const thread = await this.fetchThread(event, context.threadTs);
      const priorStatusContext = thread
        .filter((message) => message.ts !== event.ts)
        .slice(-5)
        .map(
          (message) => `${message.user === this.botUserId ? "Assistant" : "User"}: ${message.text}`,
        )
        .join("\n");
      const statusContext =
        `PRIOR CONTEXT (use only to resolve references in the current message):\n${priorStatusContext || "(none)"}\n\nCURRENT MESSAGE (the statuses must be about this):\n${event.text}`.slice(
          -1_500,
        );
      void this.agent
        .loadingStatus(statusContext)
        .then((statuses) => {
          if (statuses.length && !finished) {
            activeStatuses = statuses;
            return this.setThinking(event.channelId, context.threadTs, statuses);
          }
        })
        .catch(() => {});
      const selectedFiles = selectAttachments(thread, event.ts);
      prepared = await this.attachments.prepare(selectedFiles);
      const memories = this.memories.listForSurface({
        userId: context.requesterId,
        channelId: context.channelId,
        surface: context.surface,
      });
      const preferences = this.preferences.list(context.workspaceId, context.requesterId);
      const directives = currentDirectives();
      applicableForTurn = directives;
      const participantIds = new Set(
        thread
          .map((message) => message.user)
          .filter(
            (userId): userId is string => typeof userId === "string" && userId !== this.botUserId,
          ),
      );
      const participantNames = await this.authorizer.resolveParticipantNames(participantIds);
      const turnContext: RuntimeContext = {
        ...context,
        participantIds,
        participantNames,
      };
      const messages = threadToChatMessages({
        systemPrompt: buildSystemPrompt({
          context: turnContext,
          memories,
          preferences,
          directives,
          releasedDirectives,
          skills: this.skills.catalog(),
        }),
        messages: thread,
        botUserId: this.botUserId,
        requesterId: context.requesterId,
        requesterName: context.requesterName,
        participantNames,
        invokingTs: event.ts,
        attachmentParts: prepared.parts,
      });
      const result = await this.agent.run({
        messages,
        context: turnContext,
        directives,
        currentDirectives,
        onProgress: async (message) => {
          await this.reply(event.channelId, context.threadTs, message);
          // Slack clears assistant status asynchronously when a message posts.
          // Let that clear settle before restoring the indicator for the final answer.
          await new Promise((resolve) => setTimeout(resolve, 400));
          if (!finished) {
            await this.setThinking(event.channelId, context.threadTs, activeStatuses);
            progressStatusRestoredAt = performance.now();
          }
        },
      });
      completedWriteReceipts = result.writeReceipts;
      if (progressStatusRestoredAt !== null) {
        const remainingVisibilityMs = 1_500 - (performance.now() - progressStatusRestoredAt);
        if (remainingVisibilityMs > 0)
          await new Promise((resolve) => setTimeout(resolve, remainingVisibilityMs));
      }
      const notice = prepared.notices.length ? `${prepared.notices.join("\n")}\n\n` : "";
      const deliverySuppressedNow =
        !result.writeReceipts.length &&
        !result.images.length &&
        requiresSuppressedDelivery(currentDirectives());
      if (!result.suppressDelivery && !deliverySuppressedNow) {
        for (const [index, image] of result.images.entries()) {
          const extension =
            image.mediaType === "image/jpeg" ? "jpg" : image.mediaType.replace("image/", "");
          const defaultFilename = `mattgpt-image-${index + 1}.${extension}`;
          const filename =
            image.filename && /^[a-z0-9][a-z0-9._-]{0,99}$/i.test(image.filename)
              ? image.filename
              : defaultFilename;
          await this.slack.filesUploadV2({
            channel_id: event.channelId,
            thread_ts: context.threadTs,
            filename,
            title: image.title?.slice(0, 100) || "MattGPT generated image",
            alt_text: image.altText,
            file: image.bytes,
          });
        }
        await this.reply(
          event.channelId,
          context.threadTs,
          appendWriteReceiptFooter(`${notice}${result.text}`, result.writeReceipts),
        );
      }
      this.db.recordInteraction({
        eventId: event.eventId,
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
    } catch (error) {
      log("error", "agent_turn_failed", {
        eventId: event.eventId,
        channelId: event.channelId,
        latencyMs: Math.round(performance.now() - started),
        error: errorMessage(error),
      });
      this.db.recordInteraction({
        eventId: event.eventId,
        workspaceId: context.workspaceId,
        channelId: context.channelId,
        threadTs: context.threadTs,
        requesterId: context.requesterId,
        surface: context.surface,
        latencyMs: Math.round(performance.now() - started),
        status: "error",
        errorCode: error instanceof Error ? error.name : "unknown",
      });
      const failureText =
        error instanceof AgentRunError && error.writeReceipts.length
          ? appendWriteReceiptFooter(
              "I hit an internal error after completing the actions below.",
              error.writeReceipts,
            )
          : completedWriteReceipts.length
            ? appendWriteReceiptFooter(
                "I hit an internal error after completing the actions below.",
                completedWriteReceipts,
              )
            : "I hit an internal error and did not complete that request.";
      const suppressFailure = requiresSuppressedDelivery(
        mergeDirectives(
          applicableForTurn,
          this.directives
            .activeFor(context)
            .filter(
              (directive) =>
                directive.created_at <= eventAt && !bypassedDirectiveIds.has(directive.id),
            ),
        ),
      );
      if (!suppressFailure) {
        await this.reply(event.channelId, context.threadTs, failureText).catch(() => {});
      }
    } finally {
      finished = true;
      prepared?.cleanup();
      await this.clearThinking(event.channelId, context.threadTs);
    }
  }

  private async fetchThread(event: SlackEventInput, threadTs: string): Promise<ThreadMessage[]> {
    const messages: ThreadMessage[] = [];
    let cursor: string | undefined;
    do {
      const response = (await this.slack.conversations.replies({
        channel: event.channelId,
        ts: threadTs,
        limit: 100,
        ...(cursor ? { cursor } : {}),
      })) as {
        ok?: boolean;
        messages?: ThreadMessage[];
        response_metadata?: { next_cursor?: string };
      };
      if (!response.ok) throw new Error("Could not read the current Slack thread.");
      messages.push(...(response.messages ?? []));
      cursor = response.response_metadata?.next_cursor || undefined;
    } while (cursor && messages.length < 200);
    if (!messages.some((message) => message.ts === event.ts)) {
      messages.push({
        ts: event.ts,
        user: event.userId,
        text: event.text,
        ...(event.files ? { files: event.files } : {}),
      });
    }
    return messages;
  }

  private async reply(channel: string, threadTs: string, text: string): Promise<void> {
    for (const chunk of splitSlackResponse(removeInternalBrainLinks(text))) {
      await this.slack.chat.postMessage({
        channel,
        thread_ts: threadTs,
        text: chunk,
        blocks: [{ type: "markdown", text: chunk }],
        unfurl_links: false,
        unfurl_media: false,
      });
    }
  }

  private async setThinking(
    channelId: string,
    threadTs: string,
    statuses: readonly string[] = DEFAULT_LOADING_PROGRESSION,
  ): Promise<void> {
    const progression = statuses.slice(0, 5);
    const status = progression[0] ?? DEFAULT_LOADING_PROGRESSION[0];
    const loadingMessages = progression.slice(1);
    await this.slack.assistant.threads
      .setStatus({
        channel_id: channelId,
        thread_ts: threadTs,
        status,
        ...(loadingMessages.length ? { loading_messages: loadingMessages } : {}),
      })
      .catch(() => {});
  }

  private async clearThinking(channelId: string, threadTs: string): Promise<void> {
    await this.slack.assistant.threads
      .setStatus({ channel_id: channelId, thread_ts: threadTs, status: "" })
      .catch(() => {});
  }
}
