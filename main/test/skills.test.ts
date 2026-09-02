import { afterEach, describe, expect, test } from "bun:test";
import { MattDatabase } from "../src/db/database.ts";
import {
  BRAIN_LIBRARIAN_SKILL,
  BRAIN_LIBRARIAN_V1_INSTRUCTIONS,
  migrations,
} from "../src/db/migrations.ts";
import { SkillRepository } from "../src/db/skills.ts";
import { context, testDatabase } from "./helpers.ts";

const open: MattDatabase[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

function setup(): { db: MattDatabase; skills: SkillRepository } {
  const db = testDatabase();
  open.push(db);
  return { db, skills: new SkillRepository(db) };
}

function versionTwoDatabase(): MattDatabase {
  const db = new MattDatabase(":memory:");
  open.push(db);
  db.raw.exec(`
    CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
    ${migrations[0]?.sql ?? ""}
    ${migrations[1]?.sql ?? ""}
    INSERT INTO schema_migrations(version, applied_at) VALUES (1, 1), (2, 2);
  `);
  return db;
}

function owner(turnId: string, overrides = {}) {
  return context({ requesterId: "UOWNER", isOwner: true, turnId, ...overrides });
}

function proposal(
  skills: SkillRepository,
  turnId: string,
  overrides: Partial<{
    targetSkillId: number;
    name: string;
    description: string;
    instructions: string;
    now: number;
  }> = {},
) {
  return skills.propose({
    name: overrides.name ?? "Incident summaries",
    description: overrides.description ?? "Formats operational incident summaries",
    instructions: overrides.instructions ?? "Lead with impact, then cause and remediation.",
    ...(overrides.targetSkillId ? { targetSkillId: overrides.targetSkillId } : {}),
    context: owner(turnId),
    now: overrides.now ?? 100,
  });
}

describe("instruction skills", () => {
  test("upgrades an existing version-1 database without disturbing its data", () => {
    const db = new MattDatabase(":memory:");
    open.push(db);
    db.raw.exec(`
      CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
      ${migrations[0]?.sql ?? ""}
      INSERT INTO schema_migrations(version, applied_at) VALUES (1, 1);
      INSERT INTO memories(scope_type, scope_id, text, creator_user_id, created_at)
      VALUES ('user', 'U1', 'keep me', 'U1', 1);
    `);
    db.migrate();
    expect(db.raw.query("SELECT version FROM schema_migrations ORDER BY version").all()).toEqual([
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
    expect(db.raw.query("SELECT text FROM memories").get()).toEqual({ text: "keep me" });
    const seeded = new SkillRepository(db).catalog();
    expect(seeded).toEqual([
      expect.objectContaining({ name: BRAIN_LIBRARIAN_SKILL.name, enabled: true }),
    ]);
    expect(new SkillRepository(db).load(seeded[0]?.id ?? -1)?.instructions).toContain(
      "recommend concrete merges, renames, moves, or deletions",
    );
    expect(new SkillRepository(db).load(seeded[0]?.id ?? -1)?.instructions).toContain(
      "phrase-to-source convention",
    );
    expect(
      db.raw.query("SELECT action, actor_user_id, enabled_snapshot FROM skill_audit").get(),
    ).toEqual({ action: "created", actor_user_id: "system:figai", enabled_snapshot: 1 });
  });

  test("teaches an untouched legacy Brain Librarian about reference conventions", () => {
    const db = testDatabase();
    open.push(db);
    db.raw
      .query(`
        UPDATE skills SET instructions = ?, version = 1, updated_at = 1
        WHERE lower(name) = 'brain librarian'
      `)
      .run(BRAIN_LIBRARIAN_V1_INSTRUCTIONS);
    db.raw.query("DELETE FROM schema_migrations WHERE version = 5").run();
    db.migrate();

    const skill = db.raw
      .query<{ instructions: string; version: number }, []>(`
        SELECT instructions, version FROM skills WHERE lower(name) = 'brain librarian'
      `)
      .get();
    expect(skill?.version).toBe(2);
    expect(skill?.instructions).toContain("phrase-to-source convention");
    expect(skill?.instructions).toContain("Conventions section");
    expect(
      db.raw
        .query("SELECT action, version, actor_user_id FROM skill_audit ORDER BY id DESC LIMIT 1")
        .get(),
    ).toEqual({ action: "updated", version: 2, actor_user_id: "system:figai" });
  });

  test("does not overwrite customized Brain Librarian instructions", () => {
    const db = testDatabase();
    open.push(db);
    db.raw
      .query(`
        UPDATE skills SET instructions = 'Keep this owner customization.', version = 1,
          updated_at = 1
        WHERE lower(name) = 'brain librarian'
      `)
      .run();
    db.raw.query("DELETE FROM schema_migrations WHERE version = 5").run();
    db.migrate();
    expect(
      db.raw
        .query("SELECT instructions, version FROM skills WHERE lower(name) = 'brain librarian'")
        .get(),
    ).toEqual({ instructions: "Keep this owner customization.", version: 1 });
  });

  test("preserves an existing Brain Librarian name, including deletion history", () => {
    for (const deletedAt of [null, 100]) {
      const db = versionTwoDatabase();
      db.raw
        .query(`
          INSERT INTO skills(
            name, description, instructions, enabled, creator_user_id, created_at, updated_at,
            deleted_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        `)
        .run(
          "brain librarian",
          "Owner-authored filing rules",
          "Preserve this exact skill.",
          deletedAt === null ? 1 : 0,
          "UOWNER",
          1,
          1,
          deletedAt,
        );
      db.migrate();
      expect(db.raw.query("SELECT count(*) AS count FROM skills").get()).toEqual({ count: 1 });
      expect(db.raw.query("SELECT instructions, deleted_at FROM skills").get()).toEqual({
        instructions: "Preserve this exact skill.",
        deleted_at: deletedAt,
      });
      expect(db.raw.query("SELECT count(*) AS count FROM skill_audit").get()).toEqual({ count: 0 });
    }
  });

  test("seeds the Brain Librarian disabled when 25 skills are already enabled", () => {
    const db = versionTwoDatabase();
    const insert = db.raw.query(`
      INSERT INTO skills(name, description, instructions, creator_user_id, created_at, updated_at)
      VALUES (?, 'description', 'instructions', 'UOWNER', 1, 1)
    `);
    for (let index = 0; index < 25; index += 1) insert.run(`Skill ${index}`);
    db.migrate();
    expect(
      db.raw.query("SELECT enabled FROM skills WHERE name = ?").get(BRAIN_LIBRARIAN_SKILL.name),
    ).toEqual({ enabled: 0 });
    expect(
      db.raw
        .query("SELECT enabled_snapshot FROM skill_audit WHERE actor_user_id = 'system:figai'")
        .get(),
    ).toEqual({ enabled_snapshot: 0 });
  });

  test("requires a later same-thread confirmation and persists an audited skill", () => {
    const { db, skills } = setup();
    const draft = proposal(skills, "Ev1");
    expect(skills.catalog().map((skill) => skill.name)).toEqual([BRAIN_LIBRARIAN_SKILL.name]);
    expect(() =>
      skills.resolve({ id: draft.id, decision: "confirm", context: owner("Ev1"), now: 101 }),
    ).toThrow("later message");
    expect(() =>
      skills.resolve({
        id: draft.id,
        decision: "confirm",
        context: owner("Ev2", { threadTs: "different" }),
        now: 101,
      }),
    ).toThrow("thread where it was drafted");

    const confirmed = skills.resolve({
      id: draft.id,
      decision: "confirm",
      context: owner("Ev2"),
      now: 102,
    });
    expect(confirmed).toMatchObject({
      confirmed: true,
      cancelled: false,
      skill: { name: "Incident summaries", version: 1, enabled: true },
    });
    expect(new SkillRepository(db).load(confirmed.skill?.id ?? -1)?.instructions).toContain(
      "remediation",
    );
    expect(
      db.raw.query("SELECT action, version FROM skill_audit WHERE actor_user_id = 'UOWNER'").all(),
    ).toEqual([{ action: "created", version: 1 }]);
    expect(() => db.raw.query("UPDATE skill_audit SET action = 'deleted'").run()).toThrow(
      "immutable",
    );
    expect(() => db.raw.query("DELETE FROM skill_audit").run()).toThrow("immutable");
  });

  test("resolves the current thread's pending proposal without exposing its ID", () => {
    const { skills } = setup();
    proposal(skills, "Ev1");
    expect(() =>
      skills.resolvePending({
        decision: "confirm",
        context: owner("Ev2", { threadTs: "different" }),
        now: 101,
      }),
    ).toThrow("no pending skill proposal");
    expect(
      skills.resolvePending({ decision: "confirm", context: owner("Ev2"), now: 102 }),
    ).toMatchObject({ confirmed: true, skill: { name: "Incident summaries" } });
  });

  test("supersedes thread drafts and rejects stale revisions", () => {
    const { db, skills } = setup();
    const first = proposal(skills, "Ev1");
    const replacement = proposal(skills, "Ev2", { instructions: "Use the replacement draft." });
    expect(() =>
      skills.resolve({ id: first.id, decision: "confirm", context: owner("Ev3"), now: 103 }),
    ).toThrow("unavailable");
    const created = skills.resolve({
      id: replacement.id,
      decision: "confirm",
      context: owner("Ev3"),
      now: 104,
    });
    const skillId = created.skill?.id ?? -1;

    const stale = skills.propose({
      targetSkillId: skillId,
      name: "Incident summaries",
      description: "Formats operational incident summaries",
      instructions: "Stale instructions.",
      context: owner("Ev4", { threadTs: "thread-a" }),
      now: 105,
    });
    const current = skills.propose({
      targetSkillId: skillId,
      name: "Incident summaries",
      description: "Formats operational incident summaries",
      instructions: "Current instructions.",
      context: owner("Ev5", { threadTs: "thread-b" }),
      now: 106,
    });
    skills.resolve({
      id: current.id,
      decision: "confirm",
      context: owner("Ev6", { threadTs: "thread-b" }),
      now: 107,
    });
    expect(() =>
      skills.resolve({
        id: stale.id,
        decision: "confirm",
        context: owner("Ev7", { threadTs: "thread-a" }),
        now: 108,
      }),
    ).toThrow("changed after this preview");
    expect(skills.load(skillId)).toMatchObject({
      instructions: "Current instructions.",
      version: 2,
    });
    expect(
      db.raw
        .query("SELECT action, version FROM skill_audit WHERE actor_user_id = 'UOWNER' ORDER BY id")
        .all(),
    ).toEqual([
      { action: "created", version: 1 },
      { action: "updated", version: 2 },
    ]);
  });

  test("supports cancellation, expiry cleanup, enable, disable, and soft deletion", () => {
    const { db, skills } = setup();
    const cancelled = proposal(skills, "Ev1");
    expect(
      skills.resolve({ id: cancelled.id, decision: "cancel", context: owner("Ev1"), now: 101 }),
    ).toEqual({ confirmed: false, cancelled: true });

    proposal(skills, "Ev2", { now: 200 });
    expect(skills.pruneExpiredProposals(200 + 86_400_000)).toBe(1);

    const draft = proposal(skills, "Ev3", { now: 300 });
    const created = skills.resolve({
      id: draft.id,
      decision: "confirm",
      context: owner("Ev4"),
      now: 301,
    });
    const id = created.skill?.id ?? -1;
    expect(skills.setState({ id, state: "disabled", actorUserId: "UOWNER", now: 302 })).toEqual({
      changed: true,
      state: "disabled",
    });
    expect(skills.catalog().map((skill) => skill.name)).toEqual([BRAIN_LIBRARIAN_SKILL.name]);
    expect(skills.load(id)).toBeNull();
    expect(skills.catalog(true)).toEqual([
      expect.objectContaining({ name: BRAIN_LIBRARIAN_SKILL.name, enabled: true }),
      expect.objectContaining({ id, enabled: false }),
    ]);
    skills.setState({ id, state: "enabled", actorUserId: "UOWNER", now: 303 });
    skills.setState({ id, state: "deleted", actorUserId: "UOWNER", now: 304 });
    expect(skills.catalog(true).map((skill) => skill.name)).toEqual([BRAIN_LIBRARIAN_SKILL.name]);
    expect(
      db.raw
        .query("SELECT action FROM skill_audit WHERE actor_user_id = 'UOWNER' ORDER BY id")
        .all(),
    ).toEqual([
      { action: "created" },
      { action: "disabled" },
      { action: "enabled" },
      { action: "deleted" },
    ]);
  });

  test("enforces validation, unique names, and the active-skill cap", () => {
    const { skills } = setup();
    expect(() => proposal(skills, "Ev0", { name: "x" })).toThrow("2–64");
    expect(() => proposal(skills, "Ev0", { name: "Bad\nname" })).toThrow("single line");
    expect(() => proposal(skills, "Ev0", { description: "x".repeat(201) })).toThrow("1–200");
    expect(() => proposal(skills, "Ev0", { instructions: "x".repeat(8001) })).toThrow("1–8000");
    for (let index = 0; index < 24; index += 1) {
      const draft = proposal(skills, `draft-${index}`, {
        name: `Skill ${index}`,
        now: index * 2 + 1,
      });
      skills.resolve({
        id: draft.id,
        decision: "confirm",
        context: owner(`confirm-${index}`),
        now: index * 2 + 2,
      });
    }
    expect(() => proposal(skills, "overflow", { name: "Skill 25", now: 100 })).toThrow(
      "25 active skills",
    );
    expect(() =>
      skills.propose({
        name: "SKILL 1",
        description: "Duplicate",
        instructions: "Nope.",
        context: owner("duplicate"),
        now: 101,
      }),
    ).toThrow("already uses that name");
  });
});
