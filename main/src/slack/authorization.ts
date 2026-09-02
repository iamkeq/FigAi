import { TtlCache } from "../cache.ts";
import type { AppConfig } from "../config.ts";
import type { SlackClient } from "./client.ts";

interface SlackUser {
  id: string;
  team_id?: string;
  is_stranger?: boolean;
  is_bot?: boolean;
  deleted?: boolean;
  tz?: string;
  name?: string;
  real_name?: string;
  profile?: { display_name?: string; real_name?: string };
}

function parseUser(response: unknown): SlackUser | null {
  const value = response as { ok?: boolean; user?: SlackUser };
  return value.ok && value.user?.id ? value.user : null;
}

export interface AuthorizationResult {
  allowed: boolean;
  reason?: string;
  timezone?: string;
  requesterName?: string;
}

function safeUserName(user: SlackUser): string | null {
  const value =
    user.profile?.display_name || user.profile?.real_name || user.real_name || user.name;
  if (!value) return null;
  return (
    value
      .replace(/[\r\n\t]+/g, " ")
      .trim()
      .slice(0, 80) || null
  );
}

export class SlackAuthorizer {
  private readonly profiles = new TtlCache<SlackUser>(600_000);
  private readonly members = new TtlCache<Set<string>>(600_000);

  constructor(
    private readonly client: SlackClient,
    private readonly config: Pick<AppConfig, "allowedChannelIds" | "defaultTimezone">,
    readonly workspaceId: string,
  ) {}

  async authorize(input: {
    userId: string;
    channelId: string;
    surface: "dm" | "channel";
    fresh?: boolean;
  }): Promise<AuthorizationResult> {
    if (input.surface === "channel" && !this.config.allowedChannelIds.has(input.channelId)) {
      return { allowed: false, reason: "channel_not_allowed" };
    }
    const user = await this.user(input.userId, input.fresh);
    if (!user || user.deleted || user.is_bot) return { allowed: false, reason: "invalid_user" };
    if (user.is_stranger || user.team_id !== this.workspaceId)
      return { allowed: false, reason: "external_user" };
    if (input.surface === "dm") {
      let member = false;
      for (const channelId of this.config.allowedChannelIds) {
        if ((await this.channelMembers(channelId, input.fresh)).has(input.userId)) {
          member = true;
          break;
        }
      }
      if (!member) return { allowed: false, reason: "not_approved_member" };
    }
    return {
      allowed: true,
      timezone: user.tz || this.config.defaultTimezone,
      requesterName: safeUserName(user) ?? "Slack user",
    };
  }

  async resolveParticipantNames(userIds: Iterable<string>): Promise<ReadonlyMap<string, string>> {
    const resolved = new Map<string, string>();
    await Promise.all(
      [...new Set(userIds)].map(async (userId) => {
        try {
          const user = await this.user(userId);
          const name = user ? safeUserName(user) : null;
          if (
            user &&
            name &&
            !user.deleted &&
            !user.is_bot &&
            !user.is_stranger &&
            user.team_id === this.workspaceId
          ) {
            resolved.set(userId, name);
          }
        } catch {
          // A participant lookup should not prevent the underlying conversation from loading.
        }
      }),
    );
    return resolved;
  }

  private async user(userId: string, fresh = false): Promise<SlackUser | null> {
    const cached = fresh ? undefined : this.profiles.get(userId);
    if (cached) return cached;
    const user = parseUser(await this.client.users.info({ user: userId }));
    if (user) this.profiles.set(userId, user);
    return user;
  }

  private async channelMembers(channelId: string, fresh = false): Promise<Set<string>> {
    const cached = fresh ? undefined : this.members.get(channelId);
    if (cached) return cached;
    const members = new Set<string>();
    let cursor: string | undefined;
    do {
      const response = (await this.client.conversations.members({
        channel: channelId,
        limit: 200,
        ...(cursor ? { cursor } : {}),
      })) as { ok?: boolean; members?: string[]; response_metadata?: { next_cursor?: string } };
      if (!response.ok) throw new Error(`Could not read membership for ${channelId}.`);
      for (const user of response.members ?? []) members.add(user);
      cursor = response.response_metadata?.next_cursor || undefined;
    } while (cursor);
    this.members.set(channelId, members);
    return members;
  }
}
