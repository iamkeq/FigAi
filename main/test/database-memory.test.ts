import { afterEach, describe, expect, test } from "bun:test";
import { MattDatabase } from "../src/db/database.ts";
import { MemoryRepository } from "../src/db/memories.ts";
import { migrations } from "../src/db/migrations.ts";
import { testDatabase } from "./helpers.ts";

const open: ReturnType<typeof testDatabase>[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

describe("SQLite and memories", () => {
  test("runs numbered migrations and deduplicates event IDs", () => {
    const db = testDatabase();
    open.push(db);
    expect(db.raw.query("SELECT version FROM schema_migrations").all()).toEqual([
      { version: 1 },
      { version: 2 },
      { version: 3 },
      { version: 4 },
      { version: 5 },
      { version: 6 },
      { version: 7 },
      { version: 8 },
      { version: 9 },
      { version: 10 },
      { version: 11 },
      { version: 12 },
      { version: 13 },
      { version: 14 },
      { version: 15 },
      { version: 16 },
      { version: 17 },
    ]);
    expect(db.raw.query("PRAGMA table_info(temporary_directives)").all()).toContainEqual(
      expect.objectContaining({ name: "policy_json" }),
    );
    expect(db.raw.query("PRAGMA table_info(agent_task_runs)").all()).toContainEqual(
      expect.objectContaining({ name: "suppress_delivery", dflt_value: "0" }),
    );
    expect(db.raw.query("PRAGMA table_info(reminders)").all()).toContainEqual(
      expect.objectContaining({ name: "delivery_mode", dflt_value: "'thread'" }),
    );
    expect(db.raw.query("PRAGMA table_info(reminders)").all()).toContainEqual(
      expect.objectContaining({ name: "notification_title" }),
    );
    expect(db.raw.query("PRAGMA table_info(reminders)").all()).toContainEqual(
      expect.objectContaining({ name: "presentation_instructions" }),
    );
    expect(db.raw.query("PRAGMA table_info(workflows)").all()).toContainEqual(
      expect.objectContaining({ name: "deleted_at" }),
    );
    expect(db.claimEvent("Ev1", 1)).toBeTrue();
    expect(db.claimEvent("Ev1", 2)).toBeFalse();
    expect(db.prune(8 * 86_400_000 + 2).events).toBe(1);
  });

  test("separates personal and channel visibility", () => {
    const db = testDatabase();
    open.push(db);
    const memories = new MemoryRepository(db);
    memories.save({ scopeType: "user", scopeId: "U1", text: "personal", actorUserId: "U1" });
    memories.save({ scopeType: "channel", scopeId: "C1", text: "shared", actorUserId: "U2" });
    expect(
      memories.listForSurface({ userId: "U1", channelId: "D1", surface: "dm" }).map((m) => m.text),
    ).toEqual(["personal"]);
    expect(
      memories
        .listForSurface({ userId: "U1", channelId: "C1", surface: "channel" })
        .map((m) => m.text),
    ).toEqual(["shared"]);
  });

  test("upgrades existing reminders as cheap notification schedules", () => {
    const db = new MattDatabase(":memory:");
    open.push(db);
    db.raw.exec(`
      CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
      ${migrations[0]?.sql ?? ""}
      ${migrations[1]?.sql ?? ""}
      ${migrations[2]?.sql ?? ""}
      INSERT INTO schema_migrations(version, applied_at) VALUES (1, 1), (2, 2), (3, 3);
      INSERT INTO reminders(
        creator_user_id, workspace_id, channel_id, thread_ts, surface, text,
        timezone, recurrence, next_run_at, local_hour, local_minute, local_second,
        local_weekday, created_at, updated_at
      ) VALUES (
        'U1', 'T1', 'C1', '1.0', 'channel', 'Existing reminder',
        'UTC', 'once', 1000, 0, 0, 0, 1, 1, 1
      );
    `);
    db.migrate();
    expect(
      db.raw
        .query(
          "SELECT text, kind, delivery_mode, notification_title, presentation_instructions FROM reminders",
        )
        .get(),
    ).toEqual({
      text: "Existing reminder",
      kind: "reminder",
      delivery_mode: "thread",
      notification_title: null,
      presentation_instructions: null,
    });
  });

  test("migrates active legacy scoped directives to user-wide directives", () => {
    const db = new MattDatabase(":memory:");
    open.push(db);
    db.raw.exec(
      "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)",
    );
    for (const migration of migrations.slice(0, 10)) {
      db.raw.exec(migration.sql);
      db.raw
        .query("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)")
        .run(migration.version, migration.version);
    }
    db.raw
      .query(`
        INSERT INTO temporary_directives(
          workspace_id, user_id, scope_type, scope_id, effect, directive_text,
          release_phrase, created_at
        ) VALUES ('T1', 'U1', 'thread', 'C1:1.0', 'silence', 'Pause replies', 'resume', 1)
      `)
      .run();

    db.migrate();

    expect(
      db.raw
        .query("SELECT scope_type, scope_id, effect, starts_at FROM temporary_directives")
        .get(),
    ).toEqual({ scope_type: "global", scope_id: "*", effect: "guidance", starts_at: 1 });
  });

  test("backfills soft-deletion metadata for terminal legacy workflows", () => {
    const db = new MattDatabase(":memory:");
    open.push(db);
    db.raw.exec(
      "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)",
    );
    for (const migration of migrations.slice(0, 15)) {
      db.raw.exec(migration.sql);
      db.raw
        .query("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)")
        .run(migration.version, migration.version);
    }
    db.raw.exec(`
      INSERT INTO workflows(
        workspace_id, creator_user_id, channel_id, thread_ts, surface, delivery_mode,
        name, plan_json, current_node_id, node_entered_at, starts_at, expires_at,
        status, created_at, updated_at, finished_at
      ) VALUES (
        'T1', 'U1', 'D1', '1.0', 'dm', 'dm', 'Legacy completed workflow',
        '{"start_node":"done","nodes":[{"id":"done","type":"complete"}]}',
        'done', 10, 10, 100, 'completed', 10, 50, 50
      );
    `);
    db.migrate();
    expect(
      db.raw
        .query("SELECT status, finished_reason, deleted_at FROM workflows WHERE name = ?")
        .get("Legacy completed workflow"),
    ).toEqual({ status: "completed", finished_reason: "completed", deleted_at: 50 });
  });

  test("enforces creator/owner deletion and writes immutable audit rows", () => {
    const db = testDatabase();
    open.push(db);
    const memories = new MemoryRepository(db);
    const memory = memories.save({
      scopeType: "channel",
      scopeId: "C1",
      text: "shared",
      actorUserId: "U1",
      now: 1,
    });
    expect(() =>
      memories.delete({
        id: memory.id,
        actorUserId: "U2",
        ownerUserId: "UOWNER",
        surface: "channel",
        userId: "U2",
        channelId: "C1",
      }),
    ).toThrow("permission");
    expect(
      memories.delete({
        id: memory.id,
        actorUserId: "UOWNER",
        ownerUserId: "UOWNER",
        surface: "channel",
        userId: "UOWNER",
        channelId: "C1",
        now: 2,
      }),
    ).toBeTrue();
    expect(db.raw.query("SELECT action FROM memory_audit ORDER BY id").all()).toEqual([
      { action: "created" },
      { action: "deleted" },
    ]);
  });

  test("caps each memory scope at 100 active records", () => {
    const db = testDatabase();
    open.push(db);
    const memories = new MemoryRepository(db);
    for (let index = 0; index < 100; index += 1) {
      memories.save({
        scopeType: "user",
        scopeId: "U1",
        text: `memory ${index}`,
        actorUserId: "U1",
      });
    }
    expect(() =>
      memories.save({ scopeType: "user", scopeId: "U1", text: "one too many", actorUserId: "U1" }),
    ).toThrow("100 active");
  });
});
