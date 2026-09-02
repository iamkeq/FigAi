import { DateTime } from "luxon";
import type { Recurrence } from "../types.ts";

export interface ScheduleShape {
  timezone: string;
  recurrence: Recurrence;
  localHour: number;
  localMinute: number;
  localSecond: number;
  localWeekday: number;
}

export function parseFirstRun(value: string, timezone: string): DateTime {
  if (!DateTime.local().setZone(timezone).isValid)
    throw new Error("timezone must be a valid IANA timezone.");
  const parsed = DateTime.fromISO(value, { zone: timezone });
  if (!parsed.isValid)
    throw new Error("first_run_at must be an unambiguous ISO-8601 date and time.");
  return parsed.setZone(timezone);
}

export function scheduleShape(firstRun: DateTime, recurrence: Recurrence): ScheduleShape {
  return {
    timezone: firstRun.zoneName ?? "UTC",
    recurrence,
    localHour: firstRun.hour,
    localMinute: firstRun.minute,
    localSecond: firstRun.second,
    localWeekday: firstRun.weekday,
  };
}

function localCandidate(base: DateTime, schedule: ScheduleShape): DateTime {
  return base.set({
    hour: schedule.localHour,
    minute: schedule.localMinute,
    second: schedule.localSecond,
    millisecond: 0,
  });
}

export function nextOccurrenceAfter(afterMs: number, schedule: ScheduleShape): number | null {
  if (schedule.recurrence === "once") return null;
  const after = DateTime.fromMillis(afterMs, { zone: schedule.timezone });
  if (!after.isValid) throw new Error("Invalid reminder timezone.");
  let candidate = localCandidate(after, schedule);
  if (schedule.recurrence === "weekly") {
    const days = (schedule.localWeekday - candidate.weekday + 7) % 7;
    candidate = localCandidate(candidate.plus({ days }), schedule);
  }
  const increment = schedule.recurrence === "daily" ? { days: 1 } : { weeks: 1 };
  while (candidate.toMillis() <= afterMs)
    candidate = localCandidate(candidate.plus(increment), schedule);
  return candidate.toUTC().toMillis();
}
