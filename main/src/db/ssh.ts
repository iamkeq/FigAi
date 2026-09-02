import type { MattDatabase } from "./database.ts";

const PROPOSAL_TTL_MS = 86_400_000;

export interface SshCommandProposalRecord {
  id: number;
  host_alias: string;
  command: string;
  reason: string | null;
  creator_user_id: string;
  workspace_id: string;
  channel_id: string;
  thread_ts: string;
  origin_turn_id: string;
  created_at: number;
  expires_at: number;
  resolution: "confirmed" | "cancelled" | "superseded" | null;
  resolved_at: number | null;
}

interface SshContext {
  workspaceId: string;
  channelId: string;
  threadTs: string;
  turnId: string;
  requesterId: string;
}

function normalizedSingleLine(
  value: string,
  label: string,
  minimum: number,
  maximum: number,
): string {
  const result = value.trim();
  if (result.length < minimum || result.length > maximum) {
    throw new Error(`${label} must be ${minimum}–${maximum} characters.`);
  }
  if (/[\r\n\0]/.test(result)) throw new Error(`${label} must be a single line.`);
  return result;
}

export class SshCommandRepository {
  constructor(private readonly db: MattDatabase) {}

  propose(input: {
    hostAlias: string;
    command: string;
    reason?: string;
    context: SshContext;
    now?: number;
  }): SshCommandProposalRecord {
    const hostAlias = normalizedSingleLine(input.hostAlias, "Host alias", 1, 32);
    const command = normalizedSingleLine(input.command, "Command", 1, 4000);
    const reason = input.reason ? normalizedSingleLine(input.reason, "Reason", 1, 300) : null;
    const now = input.now ?? Date.now();
    const create = this.db.raw.transaction(() => {
      this.db.raw
        .query(`
          UPDATE ssh_command_proposals
          SET resolution = 'superseded', resolved_at = ?
          WHERE workspace_id = ? AND channel_id = ? AND thread_ts = ?
            AND creator_user_id = ? AND resolution IS NULL
        `)
        .run(
          now,
          input.context.workspaceId,
          input.context.channelId,
          input.context.threadTs,
          input.context.requesterId,
        );
      const result = this.db.raw
        .query(`
          INSERT INTO ssh_command_proposals(
            host_alias, command, reason, creator_user_id, workspace_id, channel_id, thread_ts,
            origin_turn_id, created_at, expires_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `)
        .run(
          hostAlias,
          command,
          reason,
          input.context.requesterId,
          input.context.workspaceId,
          input.context.channelId,
          input.context.threadTs,
          input.context.turnId,
          now,
          now + PROPOSAL_TTL_MS,
        );
      return Number(result.lastInsertRowid);
    });
    return this.getProposal(create());
  }

  resolvePending(input: { decision: "confirm" | "cancel"; context: SshContext; now?: number }): {
    confirmed: boolean;
    cancelled: boolean;
    proposal?: SshCommandProposalRecord;
  } {
    const now = input.now ?? Date.now();
    const proposal = this.db.raw
      .query<SshCommandProposalRecord, [string, string, string, string]>(`
        SELECT * FROM ssh_command_proposals
        WHERE workspace_id = ? AND channel_id = ? AND thread_ts = ?
          AND creator_user_id = ? AND resolution IS NULL
        ORDER BY id DESC
        LIMIT 1
      `)
      .get(
        input.context.workspaceId,
        input.context.channelId,
        input.context.threadTs,
        input.context.requesterId,
      );
    if (!proposal) throw new Error("There is no pending SSH command proposal in this thread.");
    if (proposal.expires_at <= now) throw new Error("That SSH command proposal has expired.");
    if (input.decision === "confirm" && proposal.origin_turn_id === input.context.turnId) {
      throw new Error("Review the preview and confirm it in a later message.");
    }
    if (input.decision === "cancel") {
      this.db.raw
        .query(
          "UPDATE ssh_command_proposals SET resolution = 'cancelled', resolved_at = ? WHERE id = ?",
        )
        .run(now, proposal.id);
      return { confirmed: false, cancelled: true };
    }
    this.db.raw
      .query(
        "UPDATE ssh_command_proposals SET resolution = 'confirmed', resolved_at = ? WHERE id = ?",
      )
      .run(now, proposal.id);
    return { confirmed: true, cancelled: false, proposal };
  }

  recordExecution(input: {
    proposalId: number;
    hostAlias: string;
    command: string;
    actorUserId: string;
    exitCode: number | null;
    timedOut: boolean;
    now?: number;
  }): void {
    this.db.raw
      .query(`
        INSERT INTO ssh_command_audit(
          proposal_id, host_alias, command, actor_user_id, exit_code, timed_out, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        input.proposalId,
        input.hostAlias,
        input.command,
        input.actorUserId,
        input.exitCode,
        input.timedOut ? 1 : 0,
        input.now ?? Date.now(),
      );
  }

  pruneExpiredProposals(now = Date.now()): number {
    return this.db.raw
      .query("DELETE FROM ssh_command_proposals WHERE expires_at <= ? AND resolution IS NULL")
      .run(now).changes;
  }

  private getProposal(id: number): SshCommandProposalRecord {
    const proposal = this.db.raw
      .query<SshCommandProposalRecord, [number]>("SELECT * FROM ssh_command_proposals WHERE id = ?")
      .get(id);
    if (!proposal) throw new Error("SSH command proposal did not persist.");
    return proposal;
  }
}
