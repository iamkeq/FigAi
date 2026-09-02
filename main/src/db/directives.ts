import { z } from "zod";
import type { RuntimeContext } from "../types.ts";
import type { MattDatabase } from "./database.ts";

export type DirectiveScope = "global" | "channel" | "thread";

export const DIRECTIVE_POLICY_VERSION = 1;

export const directivePolicySchema = z.object({
  version: z.literal(DIRECTIVE_POLICY_VERSION),
  kind: z.enum(["delivery_suppression", "response_constraint", "tool_restriction", "custom"]),
  delivery: z.enum(["normal", "suppress"]),
  tools: z.enum(["normal", "block_all", "semantic"]),
  requirements: z.array(z.string().trim().min(1).max(200)).max(8),
  summary: z.string().trim().min(1).max(240),
});

export type DirectivePolicy = z.infer<typeof directivePolicySchema>;

export interface DirectivePolicyCompiler {
  compileDirectivePolicy(input: {
    instruction: string;
    releaseCondition: string | null;
  }): Promise<DirectivePolicy>;
}

export const LEGACY_DIRECTIVE_POLICY: DirectivePolicy = {
  version: DIRECTIVE_POLICY_VERSION,
  kind: "custom",
  delivery: "normal",
  tools: "semantic",
  requirements: ["Interpret and enforce the original requester-authored directive semantically."],
  summary: "Legacy directive awaiting policy compilation",
};

export interface TemporaryDirectiveRecord {
  id: number;
  workspace_id: string;
  user_id: string;
  scope_type: DirectiveScope;
  scope_id: string;
  effect: "guidance" | "silence";
  directive_text: string;
  release_phrase: string | null;
  starts_at: number;
  expires_at: number | null;
  created_at: number;
  resolved_at: number | null;
  resolution: "completed" | "cancelled" | "expired" | null;
  policy_json?: string | null;
  policy_version?: number;
  policy_compiled_at?: number | null;
}

export function policyForDirective(record: TemporaryDirectiveRecord): DirectivePolicy {
  if (!record.policy_json || record.policy_version !== DIRECTIVE_POLICY_VERSION) {
    return LEGACY_DIRECTIVE_POLICY;
  }
  try {
    const parsed = directivePolicySchema.safeParse(JSON.parse(record.policy_json));
    return parsed.success ? parsed.data : LEGACY_DIRECTIVE_POLICY;
  } catch {
    return LEGACY_DIRECTIVE_POLICY;
  }
}

export function requiresSuppressedDelivery(records: TemporaryDirectiveRecord[]): boolean {
  return records.some((record) => policyForDirective(record).delivery === "suppress");
}

function normalizedPhrase(value: string): string {
  return value
    .normalize("NFKC")
    .replace(/<@[A-Z0-9]+>/gi, " ")
    .toLocaleLowerCase("en-US")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export class TemporaryDirectiveRepository {
  constructor(private readonly db: MattDatabase) {}

  create(input: {
    context: RuntimeContext;
    text: string;
    releasePhrase?: string;
    policy?: DirectivePolicy;
    startsAt?: number;
    expiresAt?: number;
    now?: number;
  }): TemporaryDirectiveRecord {
    const text = input.text.trim();
    const releasePhrase = input.releasePhrase?.trim() || null;
    if (!text || text.length > 500) throw new Error("Directive text must be 1–500 characters.");
    if (releasePhrase && (releasePhrase.length > 120 || !normalizedPhrase(releasePhrase))) {
      throw new Error("The release condition must be 1–120 readable characters.");
    }
    const now = input.now ?? Date.now();
    if (input.startsAt !== undefined && input.startsAt <= now) {
      throw new Error(
        "A scheduled temporary directive must start in the future; omit starts_at to activate it now.",
      );
    }
    const startsAt = input.startsAt ?? now;
    if (input.expiresAt !== undefined && input.expiresAt <= startsAt) {
      throw new Error("A temporary directive must expire after it starts.");
    }
    this.expire(now);
    const active = this.db.raw
      .query<{ count: number }, [string, string]>(`
        SELECT count(*) AS count FROM temporary_directives
        WHERE workspace_id = ? AND user_id = ? AND resolved_at IS NULL
      `)
      .get(input.context.workspaceId, input.context.requesterId)?.count;
    if ((active ?? 0) >= 20) {
      throw new Error("This user already has 20 active or scheduled directives.");
    }
    const result = this.db.raw
      .query(`
        INSERT INTO temporary_directives(
          workspace_id, user_id, scope_type, scope_id, effect, directive_text,
          release_phrase, starts_at, expires_at, created_at, policy_json,
          policy_version, policy_compiled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        input.context.workspaceId,
        input.context.requesterId,
        "global",
        "*",
        "guidance",
        text,
        releasePhrase,
        startsAt,
        input.expiresAt ?? null,
        now,
        input.policy ? JSON.stringify(input.policy) : null,
        input.policy?.version ?? 0,
        input.policy ? now : null,
      );
    const directive = this.get(Number(result.lastInsertRowid));
    if (!directive) throw new Error("Temporary directive did not persist.");
    return directive;
  }

  setPolicy(input: {
    id: number;
    workspaceId: string;
    userId: string;
    policy: DirectivePolicy;
    now?: number;
  }): TemporaryDirectiveRecord | null {
    const policy = directivePolicySchema.parse(input.policy);
    const changed = this.db.raw
      .query(`
        UPDATE temporary_directives
        SET policy_json = ?, policy_version = ?, policy_compiled_at = ?
        WHERE id = ? AND workspace_id = ? AND user_id = ? AND resolved_at IS NULL
      `)
      .run(
        JSON.stringify(policy),
        policy.version,
        input.now ?? Date.now(),
        input.id,
        input.workspaceId,
        input.userId,
      ).changes;
    return changed === 1 ? this.get(input.id) : null;
  }

  get(id: number): TemporaryDirectiveRecord | null {
    return (
      this.db.raw
        .query<TemporaryDirectiveRecord, [number]>(
          "SELECT * FROM temporary_directives WHERE id = ?",
        )
        .get(id) ?? null
    );
  }

  list(workspaceId: string, userId: string, now = Date.now()): TemporaryDirectiveRecord[] {
    this.expire(now);
    return this.db.raw
      .query<TemporaryDirectiveRecord, [string, string]>(`
        SELECT * FROM temporary_directives
        WHERE workspace_id = ? AND user_id = ? AND resolved_at IS NULL
        ORDER BY created_at, id
      `)
      .all(workspaceId, userId);
  }

  activeFor(context: RuntimeContext, now = Date.now()): TemporaryDirectiveRecord[] {
    return this.list(context.workspaceId, context.requesterId, now).filter(
      (record) => record.starts_at <= now,
    );
  }

  complete(ids: number[], context: RuntimeContext, now = Date.now()): number {
    return this.completeAndReturn(ids, context, now).length;
  }

  completeAndReturn(
    ids: number[],
    context: RuntimeContext,
    now = Date.now(),
  ): TemporaryDirectiveRecord[] {
    const allowed = new Map(this.activeFor(context, now).map((record) => [record.id, record]));
    const matches = [...new Set(ids)].filter((id) => allowed.has(id));
    const resolve = this.db.raw.transaction(() => {
      const completed: TemporaryDirectiveRecord[] = [];
      for (const id of matches) {
        const changed = this.db.raw
          .query(`
            UPDATE temporary_directives
            SET resolved_at = ?, resolution = 'completed'
            WHERE id = ? AND resolved_at IS NULL
          `)
          .run(now, id).changes;
        const record = allowed.get(id);
        if (changed === 1 && record) {
          completed.push({ ...record, resolved_at: now, resolution: "completed" });
        }
      }
      return completed;
    });
    return resolve();
  }

  resolve(input: { id: number; workspaceId: string; userId: string; now?: number }): boolean {
    return (
      this.db.raw
        .query(`
          UPDATE temporary_directives
          SET resolved_at = ?, resolution = 'cancelled'
          WHERE id = ? AND workspace_id = ? AND user_id = ? AND resolved_at IS NULL
        `)
        .run(input.now ?? Date.now(), input.id, input.workspaceId, input.userId).changes === 1
    );
  }

  expire(now = Date.now()): number {
    return this.db.raw
      .query(`
        UPDATE temporary_directives
        SET resolved_at = ?, resolution = 'expired'
        WHERE resolved_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?
      `)
      .run(now, now).changes;
  }
}
