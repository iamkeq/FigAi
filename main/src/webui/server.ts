import type { Agent } from "../agent/openrouter.ts";
import { threadToChatMessages } from "../agent/openrouter.ts";
import { buildSystemPrompt } from "../agent/prompt.ts";
import type { MattDatabase } from "../db/database.ts";
import { TemporaryDirectiveRepository } from "../db/directives.ts";
import type { MemoryRepository } from "../db/memories.ts";
import { UserPreferenceRepository } from "../db/preferences.ts";
import type { SkillRepository } from "../db/skills.ts";
import { errorMessage, log } from "../logger.ts";
import { MODEL_ID, type ModelControl, PRIMARY_MODEL_SETTING } from "../models.ts";
import type { RuntimeContext, ThreadMessage } from "../types.ts";
import { WEB_UI_PAGE } from "./page.ts";

const WEB_BOT_USER_ID = "webui-bot";
const WEB_CHANNEL_ID = "WEBUI";
const WEB_THREAD_TS = "webui-session";
const MAX_THREAD_MESSAGES = 200;

interface DisplayMessage {
  role: "user" | "assistant";
  text: string;
}

/**
 * A minimal chat UI over the same Agent/tool pipeline Slack uses. Binds to all
 * interfaces (reachable from the LAN at this machine's IP) with no
 * authentication, so anyone on the network can reach it with full owner-level
 * tool access. Only run this on a network you trust.
 */
export class WebUiServer {
  private server: ReturnType<typeof Bun.serve> | null = null;
  private readonly thread: ThreadMessage[] = [];
  private readonly displayLog: DisplayMessage[] = [];
  private nextTs = 1;

  constructor(
    private readonly port: number,
    private readonly agent: Agent,
    private readonly memories: MemoryRepository,
    private readonly skills: SkillRepository,
    private readonly models: ModelControl,
    private readonly db: MattDatabase,
    private readonly defaultPrimaryModel: string,
    private readonly ownerUserId: string,
    private readonly workspaceId: string,
    private readonly timezone: string,
    private readonly preferences: UserPreferenceRepository = new UserPreferenceRepository(db),
    private readonly directives: TemporaryDirectiveRepository = new TemporaryDirectiveRepository(
      db,
    ),
  ) {}

  start(): void {
    this.server = Bun.serve({
      hostname: "0.0.0.0",
      port: this.port,
      fetch: (request) => this.handle(request),
    });
    log("info", "webui_started", { port: this.port });
  }

  async stop(): Promise<void> {
    if (!this.server) return;
    await this.server.stop(true);
    this.server = null;
  }

  private async handle(request: Request): Promise<Response> {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/") {
        return new Response(WEB_UI_PAGE, { headers: { "Content-Type": "text/html" } });
      }
      if (request.method === "GET" && url.pathname === "/api/messages") {
        return Response.json({ messages: this.displayLog });
      }
      if (request.method === "GET" && url.pathname === "/api/model") {
        return Response.json({ model: this.models.getPrimaryModel() });
      }
      if (request.method === "POST" && url.pathname === "/api/model") {
        return await this.setModel(request);
      }
      if (request.method === "DELETE" && url.pathname === "/api/model") {
        this.db.deleteSetting(PRIMARY_MODEL_SETTING);
        this.models.setPrimaryModel(this.defaultPrimaryModel);
        return Response.json({ model: this.defaultPrimaryModel, reset: true });
      }
      if (request.method === "POST" && url.pathname === "/api/chat") {
        return await this.chat(request);
      }
      return new Response("Not found", { status: 404 });
    } catch (error) {
      log("error", "webui_request_failed", { error: errorMessage(error) });
      return Response.json({ error: errorMessage(error) }, { status: 500 });
    }
  }

  private async setModel(request: Request): Promise<Response> {
    const body = (await request.json().catch(() => null)) as { model?: unknown } | null;
    const requested = typeof body?.model === "string" ? body.model.trim() : "";
    if (!requested || !MODEL_ID.test(requested)) {
      return Response.json(
        { error: "Provide a model id such as provider/model." },
        { status: 400 },
      );
    }
    const resolved = await this.models.resolveModel(requested);
    if (!resolved) {
      return Response.json(
        { error: "OpenRouter does not currently list that model." },
        { status: 400 },
      );
    }
    this.db.setSetting(PRIMARY_MODEL_SETTING, resolved);
    this.models.setPrimaryModel(resolved);
    return Response.json({ model: resolved, changed: true });
  }

  private async chat(request: Request): Promise<Response> {
    const body = (await request.json().catch(() => null)) as { text?: unknown } | null;
    const text = typeof body?.text === "string" ? body.text.trim() : "";
    if (!text) return Response.json({ error: "Message text is required." }, { status: 400 });

    const userTs = String(this.nextTs++);
    this.thread.push({ ts: userTs, user: this.ownerUserId, text });
    this.displayLog.push({ role: "user", text });
    this.trimThread();

    const context: RuntimeContext = {
      workspaceId: this.workspaceId,
      botUserId: WEB_BOT_USER_ID,
      requesterId: this.ownerUserId,
      requesterName: "Owner",
      surface: "dm",
      channelId: WEB_CHANNEL_ID,
      threadTs: WEB_THREAD_TS,
      turnId: crypto.randomUUID(),
      timezone: this.timezone,
      isOwner: true,
    };
    const memories = this.memories.listForSurface({
      userId: context.requesterId,
      channelId: context.channelId,
      surface: context.surface,
    });
    const preferences = this.preferences.list(context.workspaceId, context.requesterId);
    const directives = this.directives.list(context.workspaceId, context.requesterId);
    const messages = threadToChatMessages({
      systemPrompt: buildSystemPrompt({
        context,
        memories,
        preferences,
        directives,
        skills: this.skills.catalog(),
      }),
      messages: this.thread,
      botUserId: WEB_BOT_USER_ID,
      requesterId: context.requesterId,
      requesterName: context.requesterName,
      invokingTs: userTs,
    });

    const result = await this.agent.run({
      messages,
      context,
      directives,
      currentDirectives: () => directives,
    });

    const botTs = String(this.nextTs++);
    this.thread.push({ ts: botTs, user: WEB_BOT_USER_ID, text: result.text });
    this.displayLog.push({ role: "assistant", text: result.text });
    this.trimThread();

    return Response.json({
      text: result.text,
      model: result.model,
      totalTokens: result.totalTokens,
      reportedCost: result.reportedCost,
      images: result.images.map(
        (image) => `data:${image.mediaType};base64,${image.bytes.toString("base64")}`,
      ),
    });
  }

  private trimThread(): void {
    if (this.thread.length > MAX_THREAD_MESSAGES) {
      this.thread.splice(0, this.thread.length - MAX_THREAD_MESSAGES);
    }
    if (this.displayLog.length > MAX_THREAD_MESSAGES) {
      this.displayLog.splice(0, this.displayLog.length - MAX_THREAD_MESSAGES);
    }
  }
}
