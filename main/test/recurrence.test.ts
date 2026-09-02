import { describe, expect, test } from "bun:test";
import { DateTime } from "luxon";
import { nextOccurrenceAfter, parseFirstRun, scheduleShape } from "../src/reminders/recurrence.ts";

describe("reminder recurrence", () => {
  test("daily recurrence preserves local wall time across spring DST", () => {
    const first = DateTime.fromISO("2026-03-07T09:00:00", { zone: "America/New_York" });
    const next = nextOccurrenceAfter(first.toMillis(), scheduleShape(first, "daily"));
    const local = DateTime.fromMillis(next ?? 0, { zone: "America/New_York" });
    expect(local.toISODate()).toBe("2026-03-08");
    expect(local.hour).toBe(9);
    expect((next ?? 0) - first.toMillis()).toBe(23 * 3_600_000);
  });

  test("weekly recurrence preserves local wall time across fall DST", () => {
    const first = DateTime.fromISO("2026-10-31T09:00:00", { zone: "America/New_York" });
    const next = nextOccurrenceAfter(first.toMillis(), scheduleShape(first, "weekly"));
    const local = DateTime.fromMillis(next ?? 0, { zone: "America/New_York" });
    expect(local.toISODate()).toBe("2026-11-07");
    expect(local.hour).toBe(9);
    expect((next ?? 0) - first.toMillis()).toBe(169 * 3_600_000);
  });

  test("validates timezone and ISO input", () => {
    expect(() => parseFirstRun("tomorrow-ish", "America/New_York")).toThrow("unambiguous");
    expect(() => parseFirstRun("2026-08-24T09:00:00", "Mars/Olympus")).toThrow("timezone");
  });

  test("normalizes offset-bearing input into the requested IANA timezone", () => {
    const first = parseFirstRun("2026-08-24T09:00:00-04:00", "America/New_York");
    expect(scheduleShape(first, "daily").timezone).toBe("America/New_York");
  });
});
