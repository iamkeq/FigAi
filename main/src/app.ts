import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { App } from "@slack/bolt";
import { Agent, OpenRouterClient } from "./agent/openrouter.ts";
import { ToolExecutor } from "./agent/tools.ts";
import { ScopedBrainRepository } from "./brain/scoped.ts";
import { TurnCoordinator } from "./concurrency.ts";
import type { AppConfig } from "./config.ts";
import { ActionJournalRepository } from "./db/actions.ts";
import { BackupManager } from "./db/backup.ts";
import { MattDatabase } from "./db/database.ts";
import { TemporaryDirectiveRepository } from "./db/directives.ts";
import { MemoryRepository } from "./db/memories.ts";
import { UserPreferenceRepository } from "./db/preferences.ts";
import { ReminderRepository } from "./db/reminders.ts";
import { SkillRepository } from "./db/skills.ts";
import { SshCommandRepository } from "./db/ssh.ts";
import { WorkflowRepository } from "./db/workflows.ts";
import { AttachmentManager } from "./files.ts";
import { errorMessage, log } from "./logger.ts";
import { MediaServiceClient } from "./media/client.ts";
import { PRIMARY_MODEL_SETTING } from "./models.ts";
import { SlackScheduledTaskRunner } from "./reminders/agent-runner.ts";
import { ReminderScheduler } from "./reminders/scheduler.ts";
import { SlackAuthorizer } from "./slack/authorization.ts";
import type { SlackClient } from "./slack/client.ts";
import { SlashCommands } from "./slack/commands.ts";
import { type SlackEventInput, SlackHandlers } from "./slack/handlers.ts";
import { SlackProfileService } from "./slack/profiles.ts";
import { SshClient } from "./ssh/client.ts";
import type { SlackFile } from "./types.ts";
import { SafeUrlReader } from "./web/url-reader.ts";
import { WorkflowEngine } from "./workflows/engine.ts";
import { WorkflowScheduler } from "./workflows/scheduler.ts";

export class MattGptApp {
  private readonly bolt: App;
  private readonly db: MattDatabase;
  private scheduler: ReminderScheduler | null = null;
  private workflowScheduler: WorkflowScheduler | null = null;
  private maintenance: ReturnType<typeof setInterval> | null = null;
  private stopping = false;

  constructor(private readonly config: AppConfig) {
    mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
    this.db = new MattDatabase(config.databasePath);
    this.bolt = new App({
      token: config.slackBotToken,
      appToken: config.slackAppToken,
      socketMode: true,
    });
  }

  async start(): Promise<void> {
    this.db.migrate();
    const slack = this.bolt.client as unknown as SlackClient;
    const auth = (await slack.auth.test()) as { ok?: boolean; team_id?: string; user_id?: string };
    if (!auth.ok || !auth.team_id || !auth.user_id)
      throw new Error("Slack auth.test did not return workspace and bot IDs.");
    const owner = (await slack.users.info({ user: this.config.ownerUserId })) as {
      ok?: boolean;
      user?: { team_id?: string; is_stranger?: boolean; deleted?: boolean };
    };
    if (
      !owner.ok ||
      owner.user?.team_id !== auth.team_id ||
      owner.user.is_stranger ||
      owner.user.deleted
    ) {
      throw new Error("OWNER_USER_ID is not an active internal user in this workspace.");
    }
    const channelLabels = new Map<string, string>();
    for (const channel of this.config.allowedChannelIds) {
      const result = (await slack.conversations.info({ channel })) as {
        ok?: boolean;
        channel?: { name?: string };
      };
      if (!result.ok) throw new Error(`FigAi cannot access approved channel ${channel}.`);
      channelLabels.set(channel, result.channel?.name?.trim() || "Channel");
    }

    const memories = new MemoryRepository(this.db);
    const preferences = new UserPreferenceRepository(this.db);
    const directives = new TemporaryDirectiveRepository(this.db);
    const reminders = new ReminderRepository(this.db);
    const skills = new SkillRepository(this.db);
    const workflows = new WorkflowRepository(this.db);
    const sshCommands = new SshCommandRepository(this.db);
    const ssh = this.config.sshHosts.size > 0 ? new SshClient(this.config.sshHosts) : null;
    const actions = new ActionJournalRepository(this.db);
    const backups = new BackupManager(this.db, this.config.backupDir);
    const authorizer = new SlackAuthorizer(slack, this.config, auth.team_id);
    const provider = new OpenRouterClient(this.config);
    const savedPrimaryModel = this.db.getSetting(PRIMARY_MODEL_SETTING);
    if (savedPrimaryModel) provider.setPrimaryModel(savedPrimaryModel);
    const brain = this.config.brainVaultPath
      ? new ScopedBrainRepository(
          this.config.brainVaultPath,
          join(this.config.dataDir, "brains"),
          this.config.ownerUserId,
          auth.team_id,
          this.config.allowedChannelIds,
          { channelLabels },
        )
      : null;
    const tools = new ToolExecutor(
      memories,
      reminders,
      skills,
      this.config.ownerUserId,
      this.db,
      backups,
      provider,
      this.config.primaryModel,
      brain,
      actions,
      new SafeUrlReader(),
      new MediaServiceClient(this.config.mediaConnections),
      undefined,
      preferences,
      directives,
      provider,
      workflows,
      ssh,
      sshCommands,
    );
    const agent = new Agent(
      provider,
      tools,
      new SlackProfileService(
        slack,
        this.config.slackBotToken,
        auth.team_id,
        this.config.ownerUserId,
      ),
      brain ?? undefined,
      actions,
    );
    const commands = new SlashCommands(
      this.db,
      this.config.ownerUserId,
      provider,
      this.config.primaryModel,
    );
    const turns = new TurnCoordinator(2);
    const workflowEngine = new WorkflowEngine(workflows, slack);
    const handlers = new SlackHandlers(
      this.config,
      this.db,
      slack,
      authorizer,
      memories,
      skills,
      new AttachmentManager(this.config.slackBotToken),
      agent,
      commands,
      auth.user_id,
      turns,
      preferences,
      directives,
      workflows,
      workflowEngine,
    );

    this.registerHandlers(handlers);
    await backups.createIfDue();
    await this.bolt.start();
    this.scheduler = new ReminderScheduler(
      reminders,
      slack,
      undefined,
      undefined,
      undefined,
      new SlackScheduledTaskRunner(
        this.config,
        this.db,
        slack,
        authorizer,
        memories,
        skills,
        agent,
        auth.user_id,
      ),
      turns,
    );
    this.scheduler.start();
    this.workflowScheduler = new WorkflowScheduler(workflows, workflowEngine);
    this.workflowScheduler.start();
    this.maintenance = setInterval(() => {
      try {
        const removed = this.db.prune();
        const expiredSkillProposals = skills.pruneExpiredProposals();
        const expiredSshProposals = sshCommands.pruneExpiredProposals();
        void backups
          .createIfDue()
          .catch((error) => log("error", "backup_failed", { error: errorMessage(error) }));
        log("info", "maintenance_complete", {
          ...removed,
          expiredSkillProposals,
          expiredSshProposals,
        });
      } catch (error) {
        log("error", "maintenance_failed", { error: errorMessage(error) });
      }
    }, 3_600_000);
    log("info", "figai_started", { workspaceId: auth.team_id, botUserId: auth.user_id });
  }

  async stop(): Promise<void> {
    if (this.stopping) return;
    this.stopping = true;
    await this.scheduler?.stop();
    await this.workflowScheduler?.stop();
    if (this.maintenance) clearInterval(this.maintenance);
    await this.bolt.stop();
    this.db.close();
    log("info", "figai_stopped");
  }

  private registerHandlers(handlers: SlackHandlers): void {
    this.bolt.event("app_mention", async ({ event, body }) => {
      const value = event as unknown as Record<string, unknown>;
      await handlers.handleEvent(this.normalizeEvent("app_mention", body, value));
    });
    this.bolt.event("message", async ({ event, body }) => {
      const value = event as unknown as Record<string, unknown>;
      if (typeof value.bot_id === "string") return;
      if (
        typeof value.subtype === "string" &&
        !["file_share", "thread_broadcast"].includes(value.subtype)
      )
        return;
      if (
        typeof value.user !== "string" ||
        !["im", "channel", "group"].includes(String(value.channel_type))
      )
        return;
      await handlers.handleEvent(
        this.normalizeEvent(
          value.channel_type === "im" ? "message_im" : "message_channel",
          body,
          value,
        ),
      );
    });
    this.bolt.command("/figai", async ({ ack, command, respond }) => {
      await ack();
      const text = await handlers.handleSlash({
        workspaceId: command.team_id,
        channelId: command.channel_id,
        userId: command.user_id,
        text: command.text,
      });
      await respond({ response_type: "ephemeral", text });
    });
  }

  private normalizeEvent(
    kind: "app_mention" | "message_im" | "message_channel",
    body: unknown,
    event: Record<string, unknown>,
  ): SlackEventInput {
    const envelope = body as { event_id?: string; team_id?: string };
    if (
      !envelope.event_id ||
      !envelope.team_id ||
      typeof event.channel !== "string" ||
      typeof event.user !== "string" ||
      typeof event.ts !== "string"
    ) {
      throw new Error("Slack delivered an incomplete event envelope.");
    }
    const normalized: SlackEventInput = {
      eventId: envelope.event_id,
      workspaceId: envelope.team_id,
      kind,
      channelId: event.channel,
      userId: event.user,
      ts: event.ts,
      text: typeof event.text === "string" ? event.text : "",
    };
    if (typeof event.thread_ts === "string") normalized.threadTs = event.thread_ts;
    if (typeof event.channel_type === "string") normalized.channelType = event.channel_type;
    if (typeof event.subtype === "string") normalized.subtype = event.subtype;
    if (typeof event.bot_id === "string") normalized.botId = event.bot_id;
    if (Array.isArray(event.files)) normalized.files = event.files as SlackFile[];
    return normalized;
  }
}
