import { Database } from "bun:sqlite";
import { existsSync, mkdirSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { migrations } from "./migrations.ts";

interface SessionStatsRow {
  completed_turns: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  reported_cost: number;
  latency_ms: number;
}

interface LatestInteractionRow {
  model: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  reported_cost: number | null;
  tools_json: string;
  created_at: number;
}

export class MattDatabase {
  readonly raw: Database;

  constructor(
    readonly path: string,
    options: { create?: boolean } = {},
  ) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.raw = new Database(path, { create: options.create ?? true, strict: true });
    this.raw.exec("PRAGMA journal_mode = WAL");
    this.raw.exec("PRAGMA foreign_keys = ON");
    this.raw.exec("PRAGMA busy_timeout = 5000");
  }

  migrate(): void {
    this.raw.exec(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at INTEGER NOT NULL
      )
    `);
    const applied = new Set(
      this.raw
        .query<{ version: number }, []>("SELECT version FROM schema_migrations")
        .all()
        .map((r) => r.version),
    );
    for (const migration of migrations) {
      if (applied.has(migration.version)) continue;
      const apply = this.raw.transaction(() => {
        this.raw.exec(migration.sql);
        this.raw
          .query("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)")
          .run(migration.version, Date.now());
      });
      apply();
    }
  }

  claimEvent(eventId: string, now = Date.now()): boolean {
    const result = this.raw
      .query("INSERT OR IGNORE INTO processed_events(event_id, received_at) VALUES (?, ?)")
      .run(eventId, now);
    return result.changes === 1;
  }

  recordInteraction(input: {
    eventId?: string;
    workspaceId: string;
    channelId: string;
    threadTs: string;
    requesterId: string;
    surface: "dm" | "channel";
    model?: string;
    latencyMs?: number;
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
    reportedCost?: number;
    tools?: string[];
    status: string;
    errorCode?: string;
    createdAt?: number;
  }): void {
    this.raw
      .query(`
        INSERT INTO interactions(
          event_id, workspace_id, channel_id, thread_ts, requester_id, surface,
          model, latency_ms, prompt_tokens, completion_tokens, total_tokens,
          reported_cost, tools_json, status, error_code, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        input.eventId ?? null,
        input.workspaceId,
        input.channelId,
        input.threadTs,
        input.requesterId,
        input.surface,
        input.model ?? null,
        input.latencyMs ?? null,
        input.promptTokens ?? null,
        input.completionTokens ?? null,
        input.totalTokens ?? null,
        input.reportedCost ?? null,
        JSON.stringify(input.tools ?? []),
        input.status,
        input.errorCode ?? null,
        input.createdAt ?? Date.now(),
      );
  }

  getSessionStats(input: { workspaceId: string; channelId: string; threadTs: string }): unknown {
    const parameters = [input.workspaceId, input.channelId, input.threadTs] as const;
    const totals = this.raw
      .query<SessionStatsRow, [string, string, string]>(`
        SELECT
          COUNT(*) AS completed_turns,
          COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
          COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
          COALESCE(SUM(total_tokens), 0) AS total_tokens,
          COALESCE(SUM(reported_cost), 0) AS reported_cost,
          COALESCE(SUM(latency_ms), 0) AS latency_ms
        FROM interactions
        WHERE workspace_id = ? AND channel_id = ? AND thread_ts = ? AND status = 'ok'
      `)
      .get(...parameters);
    const latest = this.raw
      .query<LatestInteractionRow, [string, string, string]>(`
        SELECT model, latency_ms, prompt_tokens, completion_tokens, total_tokens,
               reported_cost, tools_json, created_at
        FROM interactions
        WHERE workspace_id = ? AND channel_id = ? AND thread_ts = ? AND status = 'ok'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
      `)
      .get(...parameters);

    return {
      scope: "current_slack_thread",
      note: "Provider-reported usage for completed successful FigAi turns. The current in-progress request is not included.",
      totals: {
        completedTurns: totals?.completed_turns ?? 0,
        promptTokens: totals?.prompt_tokens ?? 0,
        completionTokens: totals?.completion_tokens ?? 0,
        totalTokens: totals?.total_tokens ?? 0,
        reportedCostUsd: totals?.reported_cost ?? 0,
        latencyMs: totals?.latency_ms ?? 0,
      },
      latestCompletedTurn: latest
        ? {
            model: latest.model,
            promptTokens: latest.prompt_tokens ?? 0,
            completionTokens: latest.completion_tokens ?? 0,
            totalTokens: latest.total_tokens ?? 0,
            reportedCostUsd: latest.reported_cost ?? 0,
            latencyMs: latest.latency_ms ?? 0,
            tools: JSON.parse(latest.tools_json) as unknown,
            completedAt: latest.created_at,
          }
        : null,
    };
  }

  getSetting(key: string): string | null {
    return (
      this.raw
        .query<{ value: string }, [string]>("SELECT value FROM maintenance_state WHERE key = ?")
        .get(key)?.value ?? null
    );
  }

  setSetting(key: string, value: string, now = Date.now()): void {
    this.raw
      .query(`
        INSERT INTO maintenance_state(key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
      `)
      .run(key, value, now);
  }

  deleteSetting(key: string): void {
    this.raw.query("DELETE FROM maintenance_state WHERE key = ?").run(key);
  }

  prune(now = Date.now()): {
    events: number;
    interactions: number;
    actions: number;
    directives: number;
  } {
    const events = this.raw
      .query("DELETE FROM processed_events WHERE received_at < ?")
      .run(now - 7 * 86_400_000).changes;
    const interactions = this.raw
      .query("DELETE FROM interactions WHERE created_at < ?")
      .run(now - 30 * 86_400_000).changes;
    const actions = this.raw
      .query("DELETE FROM action_journal WHERE occurred_at < ?")
      .run(now - 30 * 86_400_000).changes;
    const directives = this.raw
      .query("DELETE FROM temporary_directives WHERE resolved_at IS NOT NULL AND resolved_at < ?")
      .run(now - 30 * 86_400_000).changes;
    return { events, interactions, actions, directives };
  }

  databaseAgeMs(now = Date.now()): number | null {
    if (this.path === ":memory:") return null;
    const paths = [this.path, `${this.path}-wal`].filter(existsSync);
    if (paths.length === 0) return null;
    const newest = Math.max(...paths.map((path) => statSync(path).mtimeMs));
    return now - newest;
  }

  close(): void {
    this.raw.close(false);
  }
}
