import type { MattDatabase } from "./database.ts";

const MAX_ACTIVE_SKILLS = 25;
const PROPOSAL_TTL_MS = 86_400_000;

export interface SkillRecord {
  id: number;
  name: string;
  description: string;
  instructions: string;
  version: number;
  enabled: number;
  creator_user_id: string;
  created_at: number;
  updated_at: number;
  deleted_at: number | null;
}

export interface SkillCatalogEntry {
  id: number;
  name: string;
  description: string;
  version: number;
  enabled: boolean;
}

export interface SkillProposalRecord {
  id: number;
  operation: "create" | "update";
  target_skill_id: number | null;
  target_version: number | null;
  name: string;
  description: string;
  instructions: string;
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

interface SkillContext {
  workspaceId: string;
  channelId: string;
  threadTs: string;
  turnId: string;
  requesterId: string;
}

function normalizedText(value: string, label: string, minimum: number, maximum: number): string {
  const result = value.trim();
  if (result.length < minimum || result.length > maximum) {
    throw new Error(`${label} must be ${minimum}–${maximum} characters.`);
  }
  return result;
}

function normalizedSingleLineText(
  value: string,
  label: string,
  minimum: number,
  maximum: number,
): string {
  const result = normalizedText(value, label, minimum, maximum);
  if (/[\r\n\0]/.test(result)) throw new Error(`${label} must be a single line.`);
  return result;
}

export class SkillRepository {
  constructor(private readonly db: MattDatabase) {}

  catalog(includeDisabled = false): SkillCatalogEntry[] {
    const rows = this.db.raw
      .query<SkillRecord, [number]>(`
        SELECT * FROM skills
        WHERE deleted_at IS NULL AND (enabled = 1 OR ? = 1)
        ORDER BY lower(name), id
      `)
      .all(includeDisabled ? 1 : 0);
    return rows.map((skill) => this.catalogEntry(skill));
  }

  load(id: number, includeDisabled = false): SkillRecord | null {
    return (
      this.db.raw
        .query<SkillRecord, [number, number]>(`
          SELECT * FROM skills
          WHERE id = ? AND deleted_at IS NULL AND (enabled = 1 OR ? = 1)
        `)
        .get(id, includeDisabled ? 1 : 0) ?? null
    );
  }

  propose(input: {
    targetSkillId?: number;
    name: string;
    description: string;
    instructions: string;
    context: SkillContext;
    now?: number;
  }): SkillProposalRecord {
    const name = normalizedSingleLineText(input.name, "Skill name", 2, 64);
    const description = normalizedSingleLineText(input.description, "Skill description", 1, 200);
    const instructions = normalizedText(input.instructions, "Skill instructions", 1, 8000);
    const target = input.targetSkillId
      ? this.db.raw
          .query<SkillRecord, [number]>("SELECT * FROM skills WHERE id = ? AND deleted_at IS NULL")
          .get(input.targetSkillId)
      : null;
    if (input.targetSkillId && !target) throw new Error("That skill does not exist.");
    const conflicting = this.db.raw
      .query<{ id: number }, [string, number]>(`
        SELECT id FROM skills
        WHERE lower(name) = lower(?) AND deleted_at IS NULL AND id != ?
      `)
      .get(name, target?.id ?? -1);
    if (conflicting) throw new Error("An active skill already uses that name.");
    if (!target && this.activeCount() >= MAX_ACTIVE_SKILLS) {
      throw new Error(`FigAi already has ${MAX_ACTIVE_SKILLS} active skills.`);
    }
    const now = input.now ?? Date.now();
    const create = this.db.raw.transaction(() => {
      this.db.raw
        .query(`
          UPDATE skill_proposals
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
          INSERT INTO skill_proposals(
            operation, target_skill_id, target_version, name, description, instructions,
            creator_user_id, workspace_id, channel_id, thread_ts, origin_turn_id,
            created_at, expires_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        `)
        .run(
          target ? "update" : "create",
          target?.id ?? null,
          target?.version ?? null,
          name,
          description,
          instructions,
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

  resolve(input: {
    id: number;
    decision: "confirm" | "cancel";
    context: SkillContext;
    now?: number;
  }): { confirmed: boolean; cancelled: boolean; skill?: SkillCatalogEntry } {
    const now = input.now ?? Date.now();
    const proposal = this.db.raw
      .query<SkillProposalRecord, [number]>("SELECT * FROM skill_proposals WHERE id = ?")
      .get(input.id);
    if (!proposal || proposal.resolution) throw new Error("That skill proposal is unavailable.");
    if (proposal.expires_at <= now) throw new Error("That skill proposal has expired.");
    if (
      proposal.creator_user_id !== input.context.requesterId ||
      proposal.workspace_id !== input.context.workspaceId ||
      proposal.channel_id !== input.context.channelId ||
      proposal.thread_ts !== input.context.threadTs
    ) {
      throw new Error("Confirm this skill proposal in the thread where it was drafted.");
    }
    if (input.decision === "confirm" && proposal.origin_turn_id === input.context.turnId) {
      throw new Error("Review the preview and confirm it in a later message.");
    }
    if (input.decision === "cancel") {
      this.db.raw
        .query("UPDATE skill_proposals SET resolution = 'cancelled', resolved_at = ? WHERE id = ?")
        .run(now, proposal.id);
      return { confirmed: false, cancelled: true };
    }

    const confirm = this.db.raw.transaction(() => {
      let skill: SkillRecord;
      if (proposal.operation === "create") {
        if (this.activeCount() >= MAX_ACTIVE_SKILLS) {
          throw new Error(`FigAi already has ${MAX_ACTIVE_SKILLS} active skills.`);
        }
        const result = this.db.raw
          .query(`
            INSERT INTO skills(
              name, description, instructions, creator_user_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
          `)
          .run(
            proposal.name,
            proposal.description,
            proposal.instructions,
            input.context.requesterId,
            now,
            now,
          );
        skill = this.getRequiredSkill(Number(result.lastInsertRowid));
        this.audit(skill, "created", input.context.requesterId, now, proposal.id);
      } else {
        const current = this.getRequiredSkill(proposal.target_skill_id ?? -1);
        if (current.deleted_at !== null || current.version !== proposal.target_version) {
          throw new Error("That skill changed after this preview. Draft a new revision.");
        }
        this.db.raw
          .query(`
            UPDATE skills
            SET name = ?, description = ?, instructions = ?, version = version + 1, updated_at = ?
            WHERE id = ?
          `)
          .run(proposal.name, proposal.description, proposal.instructions, now, current.id);
        skill = this.getRequiredSkill(current.id);
        this.audit(skill, "updated", input.context.requesterId, now, proposal.id);
      }
      this.db.raw
        .query("UPDATE skill_proposals SET resolution = 'confirmed', resolved_at = ? WHERE id = ?")
        .run(now, proposal.id);
      return skill;
    });
    return { confirmed: true, cancelled: false, skill: this.catalogEntry(confirm()) };
  }

  resolvePending(input: { decision: "confirm" | "cancel"; context: SkillContext; now?: number }): {
    confirmed: boolean;
    cancelled: boolean;
    skill?: SkillCatalogEntry;
  } {
    const proposal = this.db.raw
      .query<SkillProposalRecord, [string, string, string, string]>(`
        SELECT * FROM skill_proposals
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
    if (!proposal) throw new Error("There is no pending skill proposal in this thread.");
    return this.resolve({
      id: proposal.id,
      decision: input.decision,
      context: input.context,
      ...(input.now === undefined ? {} : { now: input.now }),
    });
  }

  setState(input: {
    id: number;
    state: "enabled" | "disabled" | "deleted";
    actorUserId: string;
    now?: number;
  }): { changed: boolean; state: "enabled" | "disabled" | "deleted" } {
    const now = input.now ?? Date.now();
    const skill = this.getRequiredSkill(input.id);
    if (skill.deleted_at !== null) throw new Error("That skill has been deleted.");
    if (input.state === "enabled" && skill.enabled === 1)
      return { changed: false, state: input.state };
    if (input.state === "disabled" && skill.enabled === 0)
      return { changed: false, state: input.state };
    if (input.state === "enabled" && this.activeCount() >= MAX_ACTIVE_SKILLS) {
      throw new Error(`FigAi already has ${MAX_ACTIVE_SKILLS} active skills.`);
    }
    const change = this.db.raw.transaction(() => {
      if (input.state === "deleted") {
        this.db.raw
          .query("UPDATE skills SET enabled = 0, deleted_at = ?, updated_at = ? WHERE id = ?")
          .run(now, now, skill.id);
      } else {
        this.db.raw
          .query("UPDATE skills SET enabled = ?, updated_at = ? WHERE id = ?")
          .run(input.state === "enabled" ? 1 : 0, now, skill.id);
      }
      const updated = this.getRequiredSkill(skill.id);
      this.audit(updated, input.state, input.actorUserId, now);
    });
    change();
    return { changed: true, state: input.state };
  }

  pruneExpiredProposals(now = Date.now()): number {
    return this.db.raw
      .query("DELETE FROM skill_proposals WHERE expires_at <= ? AND resolution IS NULL")
      .run(now).changes;
  }

  private activeCount(): number {
    return (
      this.db.raw
        .query<{ count: number }, []>(
          "SELECT count(*) AS count FROM skills WHERE enabled = 1 AND deleted_at IS NULL",
        )
        .get()?.count ?? 0
    );
  }

  private getProposal(id: number): SkillProposalRecord {
    const proposal = this.db.raw
      .query<SkillProposalRecord, [number]>("SELECT * FROM skill_proposals WHERE id = ?")
      .get(id);
    if (!proposal) throw new Error("Skill proposal did not persist.");
    return proposal;
  }

  private getRequiredSkill(id: number): SkillRecord {
    const skill = this.db.raw
      .query<SkillRecord, [number]>("SELECT * FROM skills WHERE id = ?")
      .get(id);
    if (!skill) throw new Error("That skill does not exist.");
    return skill;
  }

  private audit(
    skill: SkillRecord,
    action: "created" | "updated" | "enabled" | "disabled" | "deleted",
    actorUserId: string,
    occurredAt: number,
    proposalId?: number,
  ): void {
    this.db.raw
      .query(`
        INSERT INTO skill_audit(
          skill_id, proposal_id, action, actor_user_id, version, name_snapshot,
          description_snapshot, instructions_snapshot, enabled_snapshot, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        skill.id,
        proposalId ?? null,
        action,
        actorUserId,
        skill.version,
        skill.name,
        skill.description,
        skill.instructions,
        skill.enabled,
        occurredAt,
      );
  }

  private catalogEntry(skill: SkillRecord): SkillCatalogEntry {
    return {
      id: skill.id,
      name: skill.name,
      description: skill.description,
      version: skill.version,
      enabled: skill.enabled === 1,
    };
  }
}
