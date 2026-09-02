import type { MattDatabase } from "./database.ts";

export interface MemoryRecord {
  id: number;
  scope_type: "user" | "channel";
  scope_id: string;
  text: string;
  creator_user_id: string;
  created_at: number;
}

export class MemoryRepository {
  constructor(private readonly db: MattDatabase) {}

  save(input: {
    scopeType: "user" | "channel";
    scopeId: string;
    text: string;
    actorUserId: string;
    now?: number;
  }): MemoryRecord {
    const text = input.text.trim();
    if (!text || text.length > 1000) throw new Error("Memory text must be 1–1,000 characters.");
    const count = this.db.raw
      .query<{ count: number }, [string, string]>(
        "SELECT count(*) AS count FROM memories WHERE scope_type = ? AND scope_id = ? AND deleted_at IS NULL",
      )
      .get(input.scopeType, input.scopeId)?.count;
    if ((count ?? 0) >= 100) throw new Error("This memory scope already has 100 active memories.");
    const now = input.now ?? Date.now();
    const create = this.db.raw.transaction(() => {
      const result = this.db.raw
        .query(`
          INSERT INTO memories(scope_type, scope_id, text, creator_user_id, created_at)
          VALUES (?, ?, ?, ?, ?)
        `)
        .run(input.scopeType, input.scopeId, text, input.actorUserId, now);
      const id = Number(result.lastInsertRowid);
      this.db.raw
        .query(`
          INSERT INTO memory_audit(memory_id, action, actor_user_id, scope_type, scope_id, text_snapshot, occurred_at)
          VALUES (?, 'created', ?, ?, ?, ?, ?)
        `)
        .run(id, input.actorUserId, input.scopeType, input.scopeId, text, now);
      return id;
    });
    const id = create();
    const memory = this.db.raw
      .query<MemoryRecord, [number]>("SELECT * FROM memories WHERE id = ?")
      .get(id);
    if (!memory) throw new Error("Memory insert did not persist.");
    return memory;
  }

  listForSurface(input: {
    userId: string;
    channelId: string;
    surface: "dm" | "channel";
  }): MemoryRecord[] {
    const scopeType = input.surface === "dm" ? "user" : "channel";
    const scopeId = input.surface === "dm" ? input.userId : input.channelId;
    return this.db.raw
      .query<MemoryRecord, [string, string]>(`
        SELECT id, scope_type, scope_id, text, creator_user_id, created_at
        FROM memories
        WHERE scope_type = ? AND scope_id = ? AND deleted_at IS NULL
        ORDER BY created_at ASC
      `)
      .all(scopeType, scopeId);
  }

  delete(input: {
    id: number;
    actorUserId: string;
    ownerUserId: string;
    surface: "dm" | "channel";
    userId: string;
    channelId: string;
    now?: number;
  }): boolean {
    const memory = this.db.raw
      .query<MemoryRecord, [number]>("SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL")
      .get(input.id);
    if (!memory) return false;
    const expectedType = input.surface === "dm" ? "user" : "channel";
    const expectedId = input.surface === "dm" ? input.userId : input.channelId;
    if (memory.scope_type !== expectedType || memory.scope_id !== expectedId) return false;
    const permitted =
      input.actorUserId === input.ownerUserId ||
      (memory.scope_type === "user"
        ? input.actorUserId === memory.scope_id
        : input.actorUserId === memory.creator_user_id);
    if (!permitted) throw new Error("You do not have permission to delete that memory.");
    const now = input.now ?? Date.now();
    const remove = this.db.raw.transaction(() => {
      this.db.raw
        .query("UPDATE memories SET deleted_at = ?, deleted_by = ? WHERE id = ?")
        .run(now, input.actorUserId, memory.id);
      this.db.raw
        .query(`
          INSERT INTO memory_audit(memory_id, action, actor_user_id, scope_type, scope_id, text_snapshot, occurred_at)
          VALUES (?, 'deleted', ?, ?, ?, ?, ?)
        `)
        .run(memory.id, input.actorUserId, memory.scope_type, memory.scope_id, memory.text, now);
    });
    remove();
    return true;
  }
}
