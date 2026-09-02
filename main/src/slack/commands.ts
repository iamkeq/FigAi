import type { MattDatabase } from "../db/database.ts";
import { MODEL_ID, type ModelControl, PRIMARY_MODEL_SETTING } from "../models.ts";
import type { RuntimeContext } from "../types.ts";

const SKILLS = [
  "*FigAi capabilities*",
  "Message me naturally to:",
  "• Ask questions or research the web",
  "• Read a specific public webpage without running its JavaScript",
  "• Inspect configured Sonarr, Radarr, or SABnzbd services",
  "• Add a movie to Radarr or series to Sonarr (owner only)",
  "• Search Sonarr episodes/seasons or delete selected episode files (owner DM only)",
  "• Analyze supported images, PDFs, Markdown, and text files",
  "• Generate images from a description",
  "• Inspect a requested Slack profile or avatar",
  "• Use your private DM Brain or the current approved channel's shared Brain",
  "• Remember, list, or forget information",
  "• Save response preferences or temporary behavioral directives",
  "• Create, list, or cancel reminders",
  "• Check service status or the active model",
  "• Change or reset the model (owner only)",
  "• Use owner-created instruction skills when relevant",
  "",
  "Emergency model controls:",
  "• `/figai model`",
  "• `/figai model provider/model`",
  "• `/figai model reset`",
];

export class SlashCommands {
  constructor(
    private readonly db: MattDatabase,
    private readonly ownerUserId: string,
    private readonly models: ModelControl,
    private readonly defaultPrimaryModel: string,
  ) {}

  async execute(text: string, context: RuntimeContext): Promise<string> {
    const [command = "", argument] = text.trim().split(/\s+/, 2);
    if (command.toLowerCase() !== "model") return SKILLS.join("\n");
    if (!argument) return `Primary model: \`${this.models.getPrimaryModel()}\``;
    if (context.requesterId !== this.ownerUserId) {
      return "Only the FigAi owner can change the model.";
    }
    if (argument.toLowerCase() === "reset") {
      this.db.deleteSetting(PRIMARY_MODEL_SETTING);
      this.models.setPrimaryModel(this.defaultPrimaryModel);
      return `Primary model reset to \`${this.defaultPrimaryModel}\`.`;
    }
    if (argument.length > 200 || !MODEL_ID.test(argument)) {
      return "Usage: `/figai model provider/model` or `/figai model reset`";
    }
    const resolved = await this.models.resolveModel(argument);
    if (!resolved) return "OpenRouter does not currently list that model; nothing was changed.";
    this.db.setSetting(PRIMARY_MODEL_SETTING, resolved);
    this.models.setPrimaryModel(resolved);
    return `Primary model changed to \`${resolved}\`.`;
  }
}
