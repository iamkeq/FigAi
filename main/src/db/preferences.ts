import type { MattDatabase } from "./database.ts";

export type PreferenceKey = "language" | "verbosity" | "tone" | "format" | "units";

export interface UserPreferenceRecord {
  workspace_id: string;
  user_id: string;
  preference_key: PreferenceKey;
  preference_value: string;
  created_at: number;
  updated_at: number;
}

export interface UserPreferenceValues {
  language?: string;
  verbosity?: "concise" | "balanced" | "detailed";
  tone?: "neutral" | "casual" | "formal" | "direct" | "snarky";
  format?: "prose" | "bullets" | "mixed";
  units?: "imperial" | "metric";
}

const VALID_VALUES: Record<Exclude<PreferenceKey, "language">, ReadonlySet<string>> = {
  verbosity: new Set(["concise", "balanced", "detailed"]),
  tone: new Set(["neutral", "casual", "formal", "direct", "snarky"]),
  format: new Set(["prose", "bullets", "mixed"]),
  units: new Set(["imperial", "metric"]),
};

function validatedValue(key: PreferenceKey, rawValue: string): string {
  const value = rawValue.trim();
  if (!value || value.length > 80 || /[\r\n<>`{}]/.test(value)) {
    throw new Error(`Invalid ${key} preference.`);
  }
  if (key === "language") {
    if (!/^[\p{L}][\p{L} .'-]{1,39}$/u.test(value)) throw new Error("Invalid language preference.");
    return value;
  }
  if (!VALID_VALUES[key].has(value)) throw new Error(`Invalid ${key} preference.`);
  return value;
}

export class UserPreferenceRepository {
  constructor(private readonly db: MattDatabase) {}

  set(input: {
    workspaceId: string;
    userId: string;
    values: UserPreferenceValues;
    now?: number;
  }): UserPreferenceRecord[] {
    const entries = Object.entries(input.values).filter(
      (entry): entry is [PreferenceKey, string] => typeof entry[1] === "string",
    );
    if (!entries.length) throw new Error("Choose at least one preference to update.");
    const now = input.now ?? Date.now();
    const update = this.db.raw.transaction(() => {
      for (const [key, rawValue] of entries) {
        const value = validatedValue(key, rawValue);
        this.db.raw
          .query(`
            INSERT INTO user_preferences(
              workspace_id, user_id, preference_key, preference_value, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, user_id, preference_key) DO UPDATE SET
              preference_value = excluded.preference_value,
              updated_at = excluded.updated_at
          `)
          .run(input.workspaceId, input.userId, key, value, now, now);
      }
    });
    update();
    return this.list(input.workspaceId, input.userId);
  }

  list(workspaceId: string, userId: string): UserPreferenceRecord[] {
    return this.db.raw
      .query<UserPreferenceRecord, [string, string]>(`
        SELECT workspace_id, user_id, preference_key, preference_value, created_at, updated_at
        FROM user_preferences
        WHERE workspace_id = ? AND user_id = ?
        ORDER BY preference_key
      `)
      .all(workspaceId, userId);
  }

  delete(input: { workspaceId: string; userId: string; key: PreferenceKey }): boolean {
    return (
      this.db.raw
        .query(`
          DELETE FROM user_preferences
          WHERE workspace_id = ? AND user_id = ? AND preference_key = ?
        `)
        .run(input.workspaceId, input.userId, input.key).changes === 1
    );
  }
}
