import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ModelContentPart } from "../files.ts";
import { sniffMime } from "../files.ts";
import type { RuntimeContext } from "../types.ts";
import type { SlackClient } from "./client.ts";

const MAX_AVATAR_BYTES = 10 * 1024 * 1024;
const USER_ID = /^[UW][A-Z0-9]+$/;
const IMAGE_FIELDS = [
  "image_1024",
  "image_512",
  "image_original",
  "image_192",
  "image_72",
  "image_48",
  "image_32",
  "image_24",
] as const;
const IMAGE_MIMES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);

interface SlackProfile {
  display_name?: unknown;
  real_name?: unknown;
  title?: unknown;
  status_text?: unknown;
  image_original?: unknown;
  image_1024?: unknown;
  image_512?: unknown;
  image_192?: unknown;
  image_72?: unknown;
  image_48?: unknown;
  image_32?: unknown;
  image_24?: unknown;
}

interface SlackUser {
  id?: unknown;
  team_id?: unknown;
  deleted?: unknown;
  is_bot?: unknown;
  is_stranger?: unknown;
  tz?: unknown;
  name?: unknown;
  real_name?: unknown;
  profile?: SlackProfile;
}

export interface SafeUserProfile {
  displayName: string | null;
  realName: string | null;
  title: string | null;
  timezone: string | null;
  statusText: string | null;
}

export interface PreparedUserProfile {
  profile: SafeUserProfile;
  avatarPart?: Extract<ModelContentPart, { type: "image_url" }>;
  cleanup(): void;
}

export interface UserProfileProvider {
  getUserProfile(input: {
    userId?: string;
    includeAvatar: boolean;
    context: RuntimeContext;
  }): Promise<PreparedUserProfile>;
}

export type ProfileFetcher = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

function safeText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const cleaned = [...value]
    .map((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code < 32 || code === 127 ? " " : character;
    })
    .join("")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned ? cleaned.slice(0, maxLength) : null;
}

function safeProfile(user: SlackUser): SafeUserProfile {
  return {
    displayName: safeText(user.profile?.display_name, 80) ?? safeText(user.name, 80),
    realName: safeText(user.profile?.real_name, 80) ?? safeText(user.real_name, 80),
    title: safeText(user.profile?.title, 120),
    timezone: safeText(user.tz, 80),
    statusText: safeText(user.profile?.status_text, 200),
  };
}

function avatarUrl(profile: SlackProfile | undefined): string | null {
  for (const field of IMAGE_FIELDS) {
    const value = profile?.[field];
    if (typeof value !== "string" || !value) continue;
    try {
      const url = new URL(value);
      const trustedHost =
        url.hostname === "slack.com" ||
        url.hostname.endsWith(".slack.com") ||
        url.hostname.endsWith(".slack-edge.com");
      if (url.protocol === "https:" && trustedHost) return url.toString();
    } catch {}
  }
  return null;
}

async function responseBytes(response: Response): Promise<Uint8Array> {
  const advertised = Number(response.headers.get("content-length"));
  if (Number.isFinite(advertised) && advertised > MAX_AVATAR_BYTES) {
    throw new Error("The Slack profile image is larger than the 10 MB limit.");
  }
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_AVATAR_BYTES) {
        await reader.cancel();
        throw new Error("The Slack profile image is larger than the 10 MB limit.");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

export class SlackProfileService implements UserProfileProvider {
  constructor(
    private readonly slack: SlackClient,
    private readonly botToken: string,
    private readonly workspaceId: string,
    private readonly ownerUserId: string,
    private readonly fetcher: ProfileFetcher = fetch,
  ) {}

  async getUserProfile(input: {
    userId?: string;
    includeAvatar: boolean;
    context: RuntimeContext;
  }): Promise<PreparedUserProfile> {
    const targetUserId = input.userId ?? input.context.requesterId;
    if (!USER_ID.test(targetUserId)) throw new Error("That is not a valid Slack user.");
    const permitted =
      targetUserId === input.context.requesterId ||
      targetUserId === this.ownerUserId ||
      input.context.participantIds?.has(targetUserId) === true;
    if (!permitted) {
      throw new Error("Profiles are limited to you, the owner, and people in this Slack thread.");
    }

    const response = (await this.slack.users.info({ user: targetUserId })) as {
      ok?: boolean;
      user?: SlackUser;
    };
    const user = response.ok ? response.user : undefined;
    if (!user || user.id !== targetUserId) throw new Error("Slack did not return that profile.");
    if (user.deleted || user.is_bot || user.is_stranger || user.team_id !== this.workspaceId) {
      throw new Error("That Slack profile is not an active internal user.");
    }

    const profile = safeProfile(user);
    if (!input.includeAvatar) return { profile, cleanup: () => {} };
    const url = avatarUrl(user.profile);
    if (!url) return { profile, cleanup: () => {} };

    let directory: string | null = null;
    try {
      const avatarResponse = await this.fetcher(url, {
        headers: { Authorization: `Bearer ${this.botToken}` },
        signal: AbortSignal.timeout(30_000),
      });
      if (!avatarResponse.ok) {
        throw new Error(`Slack profile image download failed (${avatarResponse.status}).`);
      }
      const declaredMime = avatarResponse.headers.get("content-type")?.split(";", 1)[0]?.trim();
      if (!declaredMime || !IMAGE_MIMES.has(declaredMime)) {
        throw new Error("Slack returned an unsupported profile image type.");
      }
      const bytes = await responseBytes(avatarResponse);
      const actualMime = sniffMime(bytes);
      if (!actualMime || actualMime !== declaredMime || !IMAGE_MIMES.has(actualMime)) {
        throw new Error("The Slack profile image content does not match its declared type.");
      }
      directory = mkdtempSync(join(tmpdir(), "mattgpt-avatar-"));
      chmodSync(directory, 0o700);
      const extension = actualMime === "image/jpeg" ? "jpg" : actualMime.replace("image/", "");
      const path = join(directory, `avatar.${extension}`);
      writeFileSync(path, bytes, { mode: 0o600 });
      const data = Buffer.from(bytes).toString("base64");
      return {
        profile,
        avatarPart: {
          type: "image_url",
          image_url: { url: `data:${actualMime};base64,${data}` },
        },
        cleanup: () => rmSync(directory as string, { recursive: true, force: true }),
      };
    } catch (error) {
      if (directory) rmSync(directory, { recursive: true, force: true });
      throw error;
    }
  }
}
