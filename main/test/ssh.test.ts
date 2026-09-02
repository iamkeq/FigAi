import { afterEach, describe, expect, test } from "bun:test";
import type { MattDatabase } from "../src/db/database.ts";
import { SshCommandRepository } from "../src/db/ssh.ts";
import { context, testDatabase } from "./helpers.ts";

const open: MattDatabase[] = [];
afterEach(() => {
  while (open.length) open.pop()?.close();
});

function setup(): { db: MattDatabase; ssh: SshCommandRepository } {
  const db = testDatabase();
  open.push(db);
  return { db, ssh: new SshCommandRepository(db) };
}

function owner(turnId: string, overrides = {}) {
  return context({ requesterId: "UOWNER", isOwner: true, turnId, ...overrides });
}

describe("ssh command proposals", () => {
  test("requires a later same-thread confirmation", () => {
    const { ssh } = setup();
    const draft = ssh.propose({
      hostAlias: "nas",
      command: "uptime",
      context: owner("Ev1"),
      now: 100,
    });
    expect(() =>
      ssh.resolvePending({ decision: "confirm", context: owner("Ev1"), now: 101 }),
    ).toThrow("later message");
    expect(() =>
      ssh.resolvePending({
        decision: "confirm",
        context: owner("Ev2", { threadTs: "different" }),
        now: 101,
      }),
    ).toThrow("no pending SSH command proposal");

    const resolved = ssh.resolvePending({ decision: "confirm", context: owner("Ev2"), now: 102 });
    expect(resolved).toMatchObject({
      confirmed: true,
      cancelled: false,
      proposal: { id: draft.id, host_alias: "nas", command: "uptime" },
    });
  });

  test("supports cancellation without ever marking the proposal confirmed", () => {
    const { ssh } = setup();
    ssh.propose({ hostAlias: "nas", command: "uptime", context: owner("Ev1"), now: 100 });
    expect(ssh.resolvePending({ decision: "cancel", context: owner("Ev2"), now: 101 })).toEqual({
      confirmed: false,
      cancelled: true,
    });
    expect(() =>
      ssh.resolvePending({ decision: "confirm", context: owner("Ev3"), now: 102 }),
    ).toThrow("no pending SSH command proposal");
  });

  test("a new proposal in the same thread supersedes an unresolved earlier draft", () => {
    const { ssh } = setup();
    ssh.propose({ hostAlias: "nas", command: "uptime", context: owner("Ev1"), now: 100 });
    const second = ssh.propose({
      hostAlias: "nas",
      command: "df -h",
      context: owner("Ev2"),
      now: 101,
    });
    const resolved = ssh.resolvePending({ decision: "confirm", context: owner("Ev3"), now: 102 });
    expect(resolved.proposal?.id).toBe(second.id);
    expect(resolved.proposal?.command).toBe("df -h");
  });

  test("expires a stale draft", () => {
    const { ssh } = setup();
    ssh.propose({ hostAlias: "nas", command: "uptime", context: owner("Ev1"), now: 0 });
    expect(() =>
      ssh.resolvePending({ decision: "confirm", context: owner("Ev2"), now: 86_400_001 }),
    ).toThrow("expired");
  });

  test("prunes expired unresolved proposals", () => {
    const { ssh } = setup();
    ssh.propose({ hostAlias: "nas", command: "uptime", context: owner("Ev1"), now: 0 });
    expect(ssh.pruneExpiredProposals(86_400_001)).toBe(1);
    expect(() =>
      ssh.resolvePending({ decision: "confirm", context: owner("Ev2"), now: 86_400_002 }),
    ).toThrow("no pending SSH command proposal");
  });

  test("records an immutable execution audit trail", () => {
    const { db, ssh } = setup();
    const draft = ssh.propose({
      hostAlias: "nas",
      command: "uptime",
      context: owner("Ev1"),
      now: 100,
    });
    ssh.recordExecution({
      proposalId: draft.id,
      hostAlias: "nas",
      command: "uptime",
      actorUserId: "UOWNER",
      exitCode: 0,
      timedOut: false,
      now: 200,
    });
    expect(
      db.raw
        .query(
          "SELECT host_alias, command, actor_user_id, exit_code, timed_out FROM ssh_command_audit",
        )
        .get(),
    ).toEqual({
      host_alias: "nas",
      command: "uptime",
      actor_user_id: "UOWNER",
      exit_code: 0,
      timed_out: 0,
    });
    expect(() => db.raw.query("UPDATE ssh_command_audit SET exit_code = 1").run()).toThrow(
      "immutable",
    );
    expect(() => db.raw.query("DELETE FROM ssh_command_audit").run()).toThrow("immutable");
  });

  test("validates command and alias length", () => {
    const { ssh } = setup();
    expect(() => ssh.propose({ hostAlias: "", command: "uptime", context: owner("Ev1") })).toThrow(
      "1–32",
    );
    expect(() =>
      ssh.propose({ hostAlias: "nas", command: "x".repeat(4001), context: owner("Ev1") }),
    ).toThrow("1–4000");
    expect(() =>
      ssh.propose({ hostAlias: "nas", command: "line one\nline two", context: owner("Ev1") }),
    ).toThrow("single line");
  });
});
