import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { SlackClient } from "../src/slack/client.ts";
import { type ProfileFetcher, SlackProfileService } from "../src/slack/profiles.ts";
import { context } from "./helpers.ts";

const fixture = (name: string) => readFileSync(join(import.meta.dir, "fixtures", name));
const tempDirectories = () =>
  new Set(readdirSync(tmpdir()).filter((name) => name.startsWith("mattgpt-avatar-")));

function slackUser(overrides: Record<string, unknown> = {}) {
  return {
    id: "UREQUESTER",
    team_id: "T123",
    tz: "America/New_York",
    profile: {
      display_name: "Display Name",
      real_name: "Real Name",
      title: "Engineer",
      status_text: "Building things",
    },
    ...overrides,
  };
}

function service(input: {
  user?: Record<string, unknown>;
  fetcher?: ProfileFetcher;
  onUserInfo?: (userId: string) => void;
}) {
  const slack = {
    users: {
      info: async ({ user }: { user: string }) => {
        input.onUserInfo?.(user);
        return { ok: true, user: input.user ?? slackUser() };
      },
    },
  } as unknown as SlackClient;
  return new SlackProfileService(slack, "xoxb-secret-token", "T123", "UOWNER", input.fetcher);
}

describe("scoped Slack profiles", () => {
  test("defaults to the requester and returns only safe profile fields", async () => {
    let requested = "";
    const prepared = await service({ onUserInfo: (userId) => (requested = userId) }).getUserProfile(
      {
        includeAvatar: false,
        context: context({ requesterId: "UREQUESTER" }),
      },
    );
    expect(requested).toBe("UREQUESTER");
    expect(prepared.profile).toEqual({
      displayName: "Display Name",
      realName: "Real Name",
      title: "Engineer",
      timezone: "America/New_York",
      statusText: "Building things",
    });
    expect(prepared).not.toHaveProperty("avatarPart");
  });

  test("permits only requester, owner, or current-thread participants", async () => {
    let lookups = 0;
    const value = service({
      user: slackUser({ id: "UPARTICIPANT" }),
      onUserInfo: () => (lookups += 1),
    });
    const scopedContext = context({
      requesterId: "UREQUESTER",
      participantIds: new Set(["UREQUESTER", "UPARTICIPANT"]),
    });
    await expect(
      value.getUserProfile({
        userId: "UPARTICIPANT",
        includeAvatar: false,
        context: scopedContext,
      }),
    ).resolves.toBeDefined();
    expect(lookups).toBe(1);
    await expect(
      value.getUserProfile({ userId: "UOUTSIDER", includeAvatar: false, context: scopedContext }),
    ).rejects.toThrow("limited");
    expect(lookups).toBe(1);

    const owner = service({ user: slackUser({ id: "UOWNER" }) });
    await expect(
      owner.getUserProfile({ userId: "UOWNER", includeAvatar: false, context: scopedContext }),
    ).resolves.toBeDefined();
  });

  test("rejects external, bot, deleted, and cross-workspace profiles", async () => {
    for (const override of [
      { is_stranger: true },
      { is_bot: true },
      { deleted: true },
      { team_id: "TOTHER" },
    ]) {
      const value = service({ user: slackUser(override) });
      await expect(
        value.getUserProfile({
          includeAvatar: false,
          context: context({ requesterId: "UREQUESTER" }),
        }),
      ).rejects.toThrow("active internal user");
    }
  });

  test("reports a missing avatar without downloading or creating files", async () => {
    let downloads = 0;
    const before = tempDirectories();
    const prepared = await service({
      fetcher: (async () => {
        downloads += 1;
        return new Response();
      }) as ProfileFetcher,
    }).getUserProfile({
      includeAvatar: true,
      context: context({ requesterId: "UREQUESTER" }),
    });
    expect(downloads).toBe(0);
    expect(prepared).not.toHaveProperty("avatarPart");
    prepared.cleanup();
    expect(tempDirectories()).toEqual(before);
  });

  test("chooses the largest supported fallback and downloads with bot auth", async () => {
    let requestedUrl = "";
    let authorization = "";
    const bytes = fixture("sample.gif");
    const prepared = await service({
      user: slackUser({
        profile: {
          image_512: "https://avatars.slack-edge.com/user-512.gif",
          image_192: "https://avatars.slack-edge.com/user-192.gif",
        },
      }),
      fetcher: (async (url: string | URL | Request, init?: RequestInit) => {
        requestedUrl = String(url);
        authorization = new Headers(init?.headers).get("Authorization") ?? "";
        return new Response(bytes, { headers: { "Content-Type": "image/gif" } });
      }) as ProfileFetcher,
    }).getUserProfile({
      includeAvatar: true,
      context: context({ requesterId: "UREQUESTER" }),
    });
    expect(requestedUrl).toBe("https://avatars.slack-edge.com/user-512.gif");
    expect(authorization).toBe("Bearer xoxb-secret-token");
    expect(prepared.avatarPart?.image_url.url).toStartWith("data:image/gif;base64,");
    prepared.cleanup();
  });

  test("uses mode-0600 temp files and removes them after success", async () => {
    const before = tempDirectories();
    const prepared = await service({
      user: slackUser({
        profile: { image_512: "https://avatars.slack-edge.com/avatar.gif" },
      }),
      fetcher: (async () =>
        new Response(fixture("sample.gif"), {
          headers: { "Content-Type": "image/gif" },
        })) as ProfileFetcher,
    }).getUserProfile({
      includeAvatar: true,
      context: context({ requesterId: "UREQUESTER" }),
    });
    const created = [...tempDirectories()].filter((name) => !before.has(name));
    expect(created).toHaveLength(1);
    const directory = join(tmpdir(), created[0] ?? "");
    expect(statSync(directory).mode & 0o777).toBe(0o700);
    expect(statSync(join(directory, "avatar.gif")).mode & 0o777).toBe(0o600);
    prepared.cleanup();
    expect(tempDirectories()).toEqual(before);
  });

  test("rejects unsupported or mismatched MIME types without temp-file leaks", async () => {
    for (const response of [
      new Response(fixture("sample.gif"), { headers: { "Content-Type": "text/html" } }),
      new Response(fixture("sample.pdf"), { headers: { "Content-Type": "image/gif" } }),
    ]) {
      const before = tempDirectories();
      const value = service({
        user: slackUser({
          profile: { image_512: "https://avatars.slack-edge.com/avatar.gif" },
        }),
        fetcher: (async () => response) as ProfileFetcher,
      });
      await expect(
        value.getUserProfile({
          includeAvatar: true,
          context: context({ requesterId: "UREQUESTER" }),
        }),
      ).rejects.toThrow();
      expect(tempDirectories()).toEqual(before);
    }
  });

  test("enforces actual size and cleans after timeout and rejection", async () => {
    const profile = slackUser({
      profile: { image_512: "https://avatars.slack-edge.com/avatar.gif" },
    });
    const before = tempDirectories();
    const oversized = service({
      user: profile,
      fetcher: (async () =>
        new Response(new Uint8Array(10 * 1024 * 1024 + 1), {
          headers: { "Content-Type": "image/gif" },
        })) as ProfileFetcher,
    });
    await expect(
      oversized.getUserProfile({
        includeAvatar: true,
        context: context({ requesterId: "UREQUESTER" }),
      }),
    ).rejects.toThrow("10 MB");

    const timedOut = service({
      user: profile,
      fetcher: (async () => {
        throw new DOMException("timed out", "TimeoutError");
      }) as ProfileFetcher,
    });
    await expect(
      timedOut.getUserProfile({
        includeAvatar: true,
        context: context({ requesterId: "UREQUESTER" }),
      }),
    ).rejects.toThrow("timed out");
    expect(tempDirectories()).toEqual(before);
  });
});
