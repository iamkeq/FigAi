import { MattDatabase } from "../src/db/database.ts";
import type { RuntimeContext } from "../src/types.ts";

export function testDatabase(): MattDatabase {
  const db = new MattDatabase(":memory:");
  db.migrate();
  return db;
}

export function context(overrides: Partial<RuntimeContext> = {}): RuntimeContext {
  return {
    workspaceId: "T123",
    botUserId: "UBOT",
    requesterId: "U123",
    requesterName: "Test User",
    surface: "channel",
    channelId: "C123",
    threadTs: "100.000",
    turnId: "Ev1",
    timezone: "America/New_York",
    isOwner: false,
    ...overrides,
  };
}
