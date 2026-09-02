import { describe, expect, test } from "bun:test";
import { KeyedMutex, Semaphore } from "../src/concurrency.ts";
import { SlackAuthorizer } from "../src/slack/authorization.ts";
import type { SlackClient } from "../src/slack/client.ts";

function slackFake(input: { external?: boolean; members?: string[]; noName?: boolean } = {}): {
  client: SlackClient;
  calls: Record<string, number>;
} {
  const calls = { users: 0, members: 0 };
  return {
    calls,
    client: {
      filesUploadV2: async () => ({ ok: true }),
      auth: { test: async () => ({ ok: true }) },
      users: {
        info: async ({ user }) => {
          calls.users += 1;
          return {
            ok: true,
            user: {
              id: user,
              team_id: "T1",
              tz: "Europe/London",
              is_stranger: input.external,
              ...(input.noName ? {} : { real_name: "Real Person" }),
              profile: input.noName ? {} : { display_name: "Display Person" },
            },
          };
        },
      },
      conversations: {
        info: async () => ({ ok: true }),
        members: async () => {
          calls.members += 1;
          return {
            ok: true,
            members: input.members ?? ["U1"],
            response_metadata: { next_cursor: "" },
          };
        },
        replies: async () => ({ ok: true, messages: [] }),
      },
      chat: { postMessage: async () => ({ ok: true }) },
      assistant: { threads: { setStatus: async () => ({ ok: true }) } },
      reactions: { add: async () => ({ ok: true }), remove: async () => ({ ok: true }) },
    },
  };
}

describe("Slack authorization", () => {
  test("allows internal DMs only for membership in an approved channel and caches for ten minutes", async () => {
    const fake = slackFake({ members: ["U1"] });
    const authorizer = new SlackAuthorizer(
      fake.client,
      { allowedChannelIds: new Set(["C1"]), defaultTimezone: "America/New_York" },
      "T1",
    );
    expect(
      (await authorizer.authorize({ userId: "U1", channelId: "D1", surface: "dm" })).allowed,
    ).toBeTrue();
    expect(
      (await authorizer.authorize({ userId: "U1", channelId: "D1", surface: "dm" })).timezone,
    ).toBe("Europe/London");
    expect(
      (await authorizer.authorize({ userId: "U1", channelId: "D1", surface: "dm" })).requesterName,
    ).toBe("Display Person");
    expect(fake.calls).toEqual({ users: 1, members: 1 });
  });

  test("denies non-members, external users, and unapproved channels", async () => {
    const memberless = slackFake({ members: [] });
    const config = { allowedChannelIds: new Set(["C1"]), defaultTimezone: "America/New_York" };
    expect(
      (
        await new SlackAuthorizer(memberless.client, config, "T1").authorize({
          userId: "U1",
          channelId: "D1",
          surface: "dm",
        })
      ).reason,
    ).toBe("not_approved_member");
    const external = slackFake({ external: true });
    expect(
      (
        await new SlackAuthorizer(external.client, config, "T1").authorize({
          userId: "U1",
          channelId: "D1",
          surface: "dm",
        })
      ).reason,
    ).toBe("external_user");
    expect(
      (
        await new SlackAuthorizer(memberless.client, config, "T1").authorize({
          userId: "U1",
          channelId: "C2",
          surface: "channel",
        })
      ).reason,
    ).toBe("channel_not_allowed");
  });

  test("resolves safe names for internal human thread participants only", async () => {
    const fake = slackFake();
    const authorizer = new SlackAuthorizer(
      fake.client,
      { allowedChannelIds: new Set(["C1"]), defaultTimezone: "America/New_York" },
      "T1",
    );
    const names = await authorizer.resolveParticipantNames(["U1", "U2", "U2"]);
    expect([...names]).toEqual([
      ["U1", "Display Person"],
      ["U2", "Display Person"],
    ]);
    expect(fake.calls.users).toBe(2);
    await authorizer.resolveParticipantNames(["U1", "U2"]);
    expect(fake.calls.users).toBe(2);

    const unnamed = slackFake({ noName: true });
    const unnamedAuthorizer = new SlackAuthorizer(
      unnamed.client,
      { allowedChannelIds: new Set(["C1"]), defaultTimezone: "America/New_York" },
      "T1",
    );
    expect([...(await unnamedAuthorizer.resolveParticipantNames(["UINTERNAL"]))]).toEqual([]);
  });
});

describe("concurrency controls", () => {
  test("serializes identical thread keys while permitting other threads", async () => {
    const mutex = new KeyedMutex();
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const first = mutex.run("same", async () => {
      order.push("first-start");
      await gate;
      order.push("first-end");
    });
    const second = mutex.run("same", async () => order.push("second"));
    const other = mutex.run("other", async () => order.push("other"));
    await Bun.sleep(0);
    expect(order).toEqual(["first-start", "other"]);
    release();
    await Promise.all([first, second, other]);
    expect(order).toEqual(["first-start", "other", "first-end", "second"]);
  });

  test("caps global work at two", async () => {
    const semaphore = new Semaphore(2);
    let active = 0;
    let peak = 0;
    await Promise.all(
      Array.from({ length: 6 }, () =>
        semaphore.run(async () => {
          active += 1;
          peak = Math.max(peak, active);
          await Bun.sleep(5);
          active -= 1;
        }),
      ),
    );
    expect(peak).toBe(2);
  });
});
