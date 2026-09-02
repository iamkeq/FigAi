import { existsSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { config as loadDotenv } from "dotenv";
import { z } from "zod";
import type { MediaConnection, MediaConnections } from "./media/client.ts";
import { defaultDataDir, defaultEnvPath } from "./platform.ts";

const serviceUrl = z
  .string()
  .url()
  .refine((value) => {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  }, "must be an HTTP(S) base URL without credentials, query parameters, or a fragment");

const schema = z
  .object({
    SLACK_BOT_TOKEN: z.string().regex(/^xoxb-/),
    SLACK_APP_TOKEN: z.string().regex(/^xapp-/),
    OPENROUTER_API_KEY: z.string().min(10),
    OWNER_USER_ID: z.string().regex(/^[UW][A-Z0-9]+$/),
    ALLOWED_CHANNEL_IDS: z
      .string()
      .transform((value) => [
        ...new Set(
          value
            .split(",")
            .map((id) => id.trim())
            .filter(Boolean),
        ),
      ])
      .pipe(z.array(z.string().regex(/^[CG][A-Z0-9]+$/)).min(1)),
    PRIMARY_MODEL: z.string().min(1).default("openai/gpt-5.6-luna"),
    FALLBACK_MODEL: z.string().min(1).default("google/gemini-3.7-flash"),
    LOADING_STATUS_MODEL: z.string().min(1).default("google/gemini-2.5-flash-lite"),
    DIRECTIVE_POLICY_MODEL: z.string().min(1).default("openai/gpt-5.6-luna"),
    IMAGE_GENERATION_MODEL: z.string().min(1).default("google/gemini-3.1-flash-lite-image"),
    OBSIDIAN_VAULT_PATH: z.string().min(1).optional(),
    SONARR_URL: serviceUrl.optional(),
    SONARR_API_KEY: z.string().min(1).optional(),
    RADARR_URL: serviceUrl.optional(),
    RADARR_API_KEY: z.string().min(1).optional(),
    SABNZBD_URL: serviceUrl.optional(),
    SABNZBD_API_KEY: z.string().min(1).optional(),
    DEFAULT_TIMEZONE: z.string().min(1).default("America/New_York"),
    LOG_LEVEL: z.enum(["debug", "info", "warn", "error"]).default("info"),
    MATTGPT_DATA_DIR: z.string().optional(),
  })
  .superRefine((value, context) => {
    for (const [service, url, key] of [
      ["Sonarr", value.SONARR_URL, value.SONARR_API_KEY],
      ["Radarr", value.RADARR_URL, value.RADARR_API_KEY],
      ["SABnzbd", value.SABNZBD_URL, value.SABNZBD_API_KEY],
    ] as const) {
      if (Boolean(url) !== Boolean(key)) {
        context.addIssue({
          code: "custom",
          message: `${service} URL and API key must be configured together`,
        });
      }
    }
  });

export interface AppConfig {
  slackBotToken: string;
  slackAppToken: string;
  openRouterApiKey: string;
  ownerUserId: string;
  allowedChannelIds: ReadonlySet<string>;
  primaryModel: string;
  fallbackModel: string;
  loadingStatusModel: string;
  directivePolicyModel: string;
  imageGenerationModel: string;
  brainVaultPath: string | null;
  mediaConnections: MediaConnections;
  defaultTimezone: string;
  logLevel: "debug" | "info" | "warn" | "error";
  dataDir: string;
  databasePath: string;
  backupDir: string;
}

function mediaConnection(baseUrl?: string, apiKey?: string): MediaConnection | null {
  if (!baseUrl || !apiKey) return null;
  return { baseUrl: baseUrl.replace(/\/+$/, ""), apiKey };
}

export function parseConfig(env: Record<string, string | undefined>): AppConfig {
  const result = schema.safeParse(env);
  if (!result.success) {
    throw new Error(`Invalid MattGPT configuration: ${z.prettifyError(result.error)}`);
  }
  const dataDir = resolve(result.data.MATTGPT_DATA_DIR ?? defaultDataDir(env));
  return {
    slackBotToken: result.data.SLACK_BOT_TOKEN,
    slackAppToken: result.data.SLACK_APP_TOKEN,
    openRouterApiKey: result.data.OPENROUTER_API_KEY,
    ownerUserId: result.data.OWNER_USER_ID,
    allowedChannelIds: new Set(result.data.ALLOWED_CHANNEL_IDS),
    primaryModel: result.data.PRIMARY_MODEL,
    fallbackModel: result.data.FALLBACK_MODEL,
    loadingStatusModel: result.data.LOADING_STATUS_MODEL,
    directivePolicyModel: result.data.DIRECTIVE_POLICY_MODEL,
    imageGenerationModel: result.data.IMAGE_GENERATION_MODEL,
    brainVaultPath: result.data.OBSIDIAN_VAULT_PATH
      ? resolve(result.data.OBSIDIAN_VAULT_PATH)
      : null,
    mediaConnections: {
      sonarr: mediaConnection(result.data.SONARR_URL, result.data.SONARR_API_KEY),
      radarr: mediaConnection(result.data.RADARR_URL, result.data.RADARR_API_KEY),
      sabnzbd: mediaConnection(result.data.SABNZBD_URL, result.data.SABNZBD_API_KEY),
    },
    defaultTimezone: result.data.DEFAULT_TIMEZONE,
    logLevel: result.data.LOG_LEVEL,
    dataDir,
    databasePath: join(dataDir, "mattgpt.sqlite"),
    backupDir: join(dataDir, "backups"),
  };
}

export function loadConfig(envPath = defaultEnvPath()): AppConfig {
  if (!existsSync(envPath)) throw new Error(`Configuration file not found: ${envPath}`);
  const mode = statSync(envPath).mode & 0o777;
  if (mode !== 0o600) {
    throw new Error(`Configuration file must have mode 0600; found ${mode.toString(8)}`);
  }
  const parsed = loadDotenv({ path: envPath, processEnv: {}, quiet: true }).parsed ?? {};
  return parseConfig({ ...process.env, ...parsed });
}
