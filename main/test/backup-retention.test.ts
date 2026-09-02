import { afterEach, describe, expect, test } from "bun:test";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ActionJournalRepository } from "../src/db/actions.ts";
import { BackupManager } from "../src/db/backup.ts";
import { MattDatabase } from "../src/db/database.ts";
import { TemporaryDirectiveRepository } from "../src/db/directives.ts";
import { UserPreferenceRepository } from "../src/db/preferences.ts";
import { SkillRepository } from "../src/db/skills.ts";
import { context } from "./helpers.ts";

const directories: string[] = [];
afterEach(() => {
  while (directories.length) {
    const directory = directories.pop();
    if (directory) rmSync(directory, { recursive: true, force: true });
  }
});

describe("retention and backups", () => {
  test("retains only seven daily SQLite backups", async () => {
    const directory = mkdtempSync(join(tmpdir(), "mattgpt-backup-test-"));
    directories.push(directory);
    const db = new MattDatabase(join(directory, "mattgpt.sqlite"));
    db.migrate();
    const skills = new SkillRepository(db);
    const draft = skills.propose({
      name: "Backup skill",
      description: "Proves skills are included in SQLite backups",
      instructions: "Remain present after backup and restore.",
      context: context({ requesterId: "UOWNER", turnId: "Ev1" }),
      now: 1,
    });
    skills.resolve({
      id: draft.id,
      decision: "confirm",
      context: context({ requesterId: "UOWNER", turnId: "Ev2" }),
      now: 2,
    });
    const manager = new BackupManager(db, join(directory, "backups"));
    const start = Date.UTC(2026, 0, 1);
    for (let day = 0; day < 9; day += 1) await manager.createIfDue(start + day * 86_400_001);
    expect(readdirSync(join(directory, "backups"))).toHaveLength(7);
    expect(manager.latestAgeMs(start + 9 * 86_400_001)).toBeGreaterThanOrEqual(86_400_000);
    const latest = readdirSync(join(directory, "backups")).sort().at(-1);
    const restored = new MattDatabase(join(directory, "backups", latest ?? "missing"), {
      create: false,
    });
    expect(restored.raw.query("SELECT name FROM skills ORDER BY name").all()).toEqual([
      { name: "Backup skill" },
      { name: "Brain Librarian" },
    ]);
    restored.close();
    db.close();
  });

  test("prunes interactions and action metadata after 30 days but leaves explicit memory", () => {
    const db = new MattDatabase(":memory:");
    db.migrate();
    db.recordInteraction({
      workspaceId: "T1",
      channelId: "C1",
      threadTs: "1",
      requesterId: "U1",
      surface: "channel",
      status: "ok",
      createdAt: 1,
    });
    db.raw
      .query(
        "INSERT INTO memories(scope_type, scope_id, text, creator_user_id, created_at) VALUES ('user', 'U1', 'keep', 'U1', 1)",
      )
      .run();
    new ActionJournalRepository(db).recordToolCall({
      toolName: "brain_search",
      toolResult: { ok: true, result: { notes: [] } },
      context: context(),
      now: 1,
    });
    new UserPreferenceRepository(db).set({
      workspaceId: "T123",
      userId: "U123",
      values: { verbosity: "concise" },
      now: 1,
    });
    const directives = new TemporaryDirectiveRepository(db);
    const directive = directives.create({
      context: context(),
      text: "Pause replies.",
      releasePhrase: "resume",
      now: 1,
    });
    directives.resolve({
      id: directive.id,
      workspaceId: "T123",
      userId: "U123",
      now: 2,
    });
    expect(db.prune(31 * 86_400_000)).toMatchObject({
      interactions: 1,
      actions: 1,
      directives: 1,
    });
    expect(db.raw.query("SELECT count(*) AS count FROM memories").get()).toEqual({ count: 1 });
    expect(db.raw.query("SELECT count(*) AS count FROM action_journal").get()).toEqual({
      count: 0,
    });
    expect(db.raw.query("SELECT count(*) AS count FROM temporary_directives").get()).toEqual({
      count: 0,
    });
    expect(db.raw.query("SELECT count(*) AS count FROM user_preferences").get()).toEqual({
      count: 1,
    });
    db.close();
  });
});
