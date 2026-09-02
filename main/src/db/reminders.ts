import { randomUUID } from "node:crypto";
import type { DateTime } from "luxon";
import { nextOccurrenceAfter, type ScheduleShape, scheduleShape } from "../reminders/recurrence.ts";
import type { Recurrence, RuntimeContext, ScheduleDelivery, ScheduleKind } from "../types.ts";
import type { MattDatabase } from "./database.ts";

export interface ReminderRecord {
  id: number;
  creator_user_id: string;
  workspace_id: string;
  channel_id: string;
  thread_ts: string;
  surface: "dm" | "channel";
  text: string;
  timezone: string;
  recurrence: Recurrence;
  next_run_at: number;
  local_hour: number;
  local_minute: number;
  local_second: number;
  local_weekday: number;
  lease_token: string | null;
  lease_expires_at: number | null;
  attempt_count: number;
  last_error: string | null;
  created_at: number;
  updated_at: number;
  cancelled_at: number | null;
  completed_at: number | null;
  kind: ScheduleKind;
  delivery_mode: ScheduleDelivery;
  notification_title: string | null;
  presentation_instructions: string | null;
}

export interface AgentTaskRunRecord {
  id: number;
  reminder_id: number;
  scheduled_for: number;
  status: "running" | "ready" | "failed" | "delivered";
  response_text: string | null;
  suppress_delivery: number;
  write_performed: number;
  started_at: number;
  finished_at: number | null;
  error: string | null;
}

function shape(row: ReminderRecord): ScheduleShape {
  return {
    timezone: row.timezone,
    recurrence: row.recurrence,
    localHour: row.local_hour,
    localMinute: row.local_minute,
    localSecond: row.local_second,
    localWeekday: row.local_weekday,
  };
}

export class ReminderRepository {
  constructor(private readonly db: MattDatabase) {}

  create(input: {
    context: RuntimeContext;
    text: string;
    firstRun: DateTime;
    recurrence: Recurrence;
    kind?: ScheduleKind;
    delivery?: ScheduleDelivery;
    notificationTitle?: string;
    presentationInstructions?: string;
    now?: number;
  }): ReminderRecord {
    const text = input.text.trim();
    const notificationTitle = input.notificationTitle?.trim() || null;
    const presentationInstructions = input.presentationInstructions?.trim() || null;
    const label = input.kind === "agent_task" ? "Scheduled task command" : "Reminder text";
    if (!text || text.length > 1000) throw new Error(`${label} must be 1–1,000 characters.`);
    if (notificationTitle && notificationTitle.length > 80) {
      throw new Error("Notification title must be at most 80 characters.");
    }
    if (notificationTitle && /[\r\n]/.test(notificationTitle)) {
      throw new Error("Notification title must be a single line.");
    }
    if (presentationInstructions && presentationInstructions.length > 600) {
      throw new Error("Presentation instructions must be at most 600 characters.");
    }
    const now = input.now ?? Date.now();
    if (input.firstRun.toMillis() <= now)
      throw new Error("The first reminder time must be in the future.");
    const counts = this.db.raw
      .query<{ active: number; recurring: number }, [string]>(`
        SELECT count(*) AS active,
          sum(CASE WHEN recurrence != 'once' THEN 1 ELSE 0 END) AS recurring
        FROM reminders
        WHERE creator_user_id = ? AND cancelled_at IS NULL AND completed_at IS NULL
      `)
      .get(input.context.requesterId);
    if ((counts?.active ?? 0) >= 25)
      throw new Error("You already have 25 active reminders or scheduled tasks.");
    if (input.recurrence !== "once" && (counts?.recurring ?? 0) >= 10) {
      throw new Error("You already have 10 recurring reminders or scheduled tasks.");
    }
    const schedule = scheduleShape(input.firstRun, input.recurrence);
    const result = this.db.raw
      .query(`
        INSERT INTO reminders(
          creator_user_id, workspace_id, channel_id, thread_ts, surface, text,
          timezone, recurrence, next_run_at, local_hour, local_minute, local_second,
          local_weekday, created_at, updated_at, kind, delivery_mode,
          notification_title, presentation_instructions
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `)
      .run(
        input.context.requesterId,
        input.context.workspaceId,
        input.context.channelId,
        input.context.threadTs,
        input.context.surface,
        text,
        schedule.timezone,
        input.recurrence,
        input.firstRun.toUTC().toMillis(),
        schedule.localHour,
        schedule.localMinute,
        schedule.localSecond,
        schedule.localWeekday,
        now,
        now,
        input.kind ?? "reminder",
        input.delivery ?? "thread",
        notificationTitle,
        presentationInstructions,
      );
    const reminder = this.get(Number(result.lastInsertRowid));
    if (!reminder) throw new Error("Reminder insert did not persist.");
    return reminder;
  }

  claimAgentRun(
    row: ReminderRecord,
    now: number,
  ): {
    state: "started" | "running" | "ready" | "delivered" | "failed";
    run: AgentTaskRunRecord;
  } {
    if (row.kind !== "agent_task") throw new Error("Only agent tasks have agent runs.");
    const inserted = this.db.raw
      .query(`
        INSERT OR IGNORE INTO agent_task_runs(
          reminder_id, scheduled_for, status, started_at
        ) VALUES (?, ?, 'running', ?)
      `)
      .run(row.id, row.next_run_at, now);
    const run = this.db.raw
      .query<AgentTaskRunRecord, [number, number]>(`
        SELECT * FROM agent_task_runs WHERE reminder_id = ? AND scheduled_for = ?
      `)
      .get(row.id, row.next_run_at);
    if (!run) throw new Error("Agent task run did not persist.");
    return {
      state: inserted.changes === 1 ? "started" : run.status,
      run,
    };
  }

  markAgentReady(
    row: ReminderRecord,
    responseText: string,
    writePerformed: boolean,
    now: number,
    suppressDelivery = false,
  ): void {
    this.db.raw
      .query(`
        UPDATE agent_task_runs SET status = 'ready', response_text = ?, write_performed = ?,
          suppress_delivery = ?, finished_at = ?, error = NULL
        WHERE reminder_id = ? AND scheduled_for = ? AND status = 'running'
      `)
      .run(
        responseText,
        writePerformed ? 1 : 0,
        suppressDelivery ? 1 : 0,
        now,
        row.id,
        row.next_run_at,
      );
  }

  markAgentRunFailed(
    row: ReminderRecord,
    error: string,
    writePerformed: boolean,
    now: number,
  ): void {
    this.db.raw
      .query(`
        UPDATE agent_task_runs SET status = 'failed',
          write_performed = CASE WHEN write_performed = 1 OR ? = 1 THEN 1 ELSE 0 END,
          finished_at = ?, error = ?
        WHERE reminder_id = ? AND scheduled_for = ? AND status IN ('running', 'ready')
      `)
      .run(writePerformed ? 1 : 0, now, error.slice(0, 500), row.id, row.next_run_at);
  }

  disable(row: ReminderRecord, error: string, now: number): void {
    this.db.raw
      .query(`
        UPDATE reminders SET cancelled_at = ?, updated_at = ?, last_error = ?,
          lease_token = NULL, lease_expires_at = NULL
        WHERE id = ? AND cancelled_at IS NULL AND completed_at IS NULL
      `)
      .run(now, now, error.slice(0, 500), row.id);
  }

  get(id: number): ReminderRecord | null {
    return (
      this.db.raw.query<ReminderRecord, [number]>("SELECT * FROM reminders WHERE id = ?").get(id) ??
      null
    );
  }

  list(context: RuntimeContext): ReminderRecord[] {
    return this.db.raw
      .query<ReminderRecord, [string, string, string]>(`
        SELECT * FROM reminders
        WHERE creator_user_id = ? AND channel_id = ? AND surface = ?
          AND cancelled_at IS NULL AND completed_at IS NULL
        ORDER BY next_run_at ASC
      `)
      .all(context.requesterId, context.channelId, context.surface);
  }

  cancel(input: {
    id: number;
    actorUserId: string;
    ownerUserId: string;
    context: RuntimeContext;
    now?: number;
  }): boolean {
    const reminder = this.get(input.id);
    if (!reminder || reminder.cancelled_at || reminder.completed_at) return false;
    if (
      reminder.channel_id !== input.context.channelId ||
      reminder.surface !== input.context.surface
    )
      return false;
    if (input.actorUserId !== reminder.creator_user_id && input.actorUserId !== input.ownerUserId) {
      throw new Error("You do not have permission to cancel that reminder.");
    }
    return (
      this.db.raw
        .query(`
          UPDATE reminders SET cancelled_at = ?, updated_at = ?, lease_token = NULL, lease_expires_at = NULL
          WHERE id = ? AND cancelled_at IS NULL AND completed_at IS NULL
        `)
        .run(input.now ?? Date.now(), input.now ?? Date.now(), input.id).changes === 1
    );
  }

  leaseDue(now: number, leaseMs = 60_000, limit = 10): ReminderRecord[] {
    const token = randomUUID();
    const lease = this.db.raw.transaction(() => {
      const ids = this.db.raw
        .query<{ id: number }, [number, number, number]>(`
          SELECT id FROM reminders
          WHERE next_run_at <= ? AND cancelled_at IS NULL AND completed_at IS NULL
            AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
          ORDER BY CASE kind WHEN 'reminder' THEN 0 ELSE 1 END, next_run_at ASC LIMIT ?
        `)
        .all(now, now, limit)
        .map((row) => row.id);
      for (const id of ids) {
        this.db.raw
          .query(
            "UPDATE reminders SET lease_token = ?, lease_expires_at = ?, updated_at = ? WHERE id = ?",
          )
          .run(token, now + leaseMs, now, id);
      }
      return ids;
    });
    const ids = lease();
    if (ids.length === 0) return [];
    return this.db.raw
      .query<ReminderRecord, [string]>(
        "SELECT * FROM reminders WHERE lease_token = ? ORDER BY next_run_at",
      )
      .all(token);
  }

  markDelivered(row: ReminderRecord, deliveredAt: number, late: boolean, attempts = 1): void {
    const update = this.db.raw.transaction(() => {
      this.db.raw
        .query(`
          INSERT INTO reminder_deliveries(
            reminder_id, scheduled_for, delivered_at, status, attempt_count, late
          ) VALUES (?, ?, ?, 'delivered', ?, ?)
          ON CONFLICT(reminder_id, scheduled_for) DO UPDATE SET
            delivered_at = excluded.delivered_at,
            status = 'delivered',
            attempt_count = excluded.attempt_count,
            error = NULL,
            late = excluded.late
        `)
        .run(row.id, row.next_run_at, deliveredAt, row.attempt_count + attempts, late ? 1 : 0);
      if (row.kind === "agent_task") {
        this.db.raw
          .query(`
            UPDATE agent_task_runs SET status = 'delivered', finished_at = ?
            WHERE reminder_id = ? AND scheduled_for = ?
          `)
          .run(deliveredAt, row.id, row.next_run_at);
      }
      const next = nextOccurrenceAfter(deliveredAt, shape(row));
      if (next === null) {
        this.db.raw
          .query(`
            UPDATE reminders SET completed_at = ?, updated_at = ?, attempt_count = 0,
              last_error = NULL, lease_token = NULL, lease_expires_at = NULL WHERE id = ?
          `)
          .run(deliveredAt, deliveredAt, row.id);
      } else {
        this.db.raw
          .query(`
            UPDATE reminders SET next_run_at = ?, updated_at = ?, attempt_count = 0,
              last_error = NULL, lease_token = NULL, lease_expires_at = NULL WHERE id = ?
          `)
          .run(next, deliveredAt, row.id);
      }
    });
    update();
  }

  markOccurrenceFailed(row: ReminderRecord, error: string, now: number, attempts = 1): void {
    const update = this.db.raw.transaction(() => {
      this.db.raw
        .query(`
          INSERT OR REPLACE INTO reminder_deliveries(
            reminder_id, scheduled_for, status, attempt_count, error, late
          ) VALUES (?, ?, 'failed', ?, ?, ?)
        `)
        .run(
          row.id,
          row.next_run_at,
          row.attempt_count + attempts,
          error.slice(0, 500),
          now > row.next_run_at ? 1 : 0,
        );
      const next = nextOccurrenceAfter(now, shape(row));
      if (next === null) {
        this.db.raw
          .query(`
            UPDATE reminders SET completed_at = ?, updated_at = ?, attempt_count = ?,
              last_error = ?, lease_token = NULL, lease_expires_at = NULL WHERE id = ?
          `)
          .run(now, now, row.attempt_count + attempts, error.slice(0, 500), row.id);
      } else {
        this.db.raw
          .query(`
            UPDATE reminders SET next_run_at = ?, updated_at = ?, attempt_count = 0,
              last_error = ?, lease_token = NULL, lease_expires_at = NULL WHERE id = ?
          `)
          .run(next, now, error.slice(0, 500), row.id);
      }
    });
    update();
  }

  markAttemptFailure(
    row: ReminderRecord,
    error: string,
    now: number,
    final: boolean,
    attempts = 1,
  ): void {
    if (final) {
      this.db.raw
        .query(`
          INSERT OR REPLACE INTO reminder_deliveries(
            reminder_id, scheduled_for, status, attempt_count, error, late
          ) VALUES (?, ?, 'failed', ?, ?, ?)
        `)
        .run(
          row.id,
          row.next_run_at,
          row.attempt_count + attempts,
          error.slice(0, 500),
          now > row.next_run_at ? 1 : 0,
        );
    }
    this.db.raw
      .query(`
        UPDATE reminders SET attempt_count = ?, last_error = ?, updated_at = ?,
          lease_token = NULL, lease_expires_at = ? WHERE id = ?
      `)
      .run(
        row.attempt_count + attempts,
        error.slice(0, 500),
        now,
        final ? now + 3_600_000 : now,
        row.id,
      );
  }
}
