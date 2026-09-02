import { mkdirSync, readdirSync, statSync, unlinkSync, utimesSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { MattDatabase } from "./database.ts";

const DAY_MS = 86_400_000;

export class BackupManager {
  constructor(
    private readonly db: MattDatabase,
    private readonly directory: string,
  ) {}

  latestAgeMs(now = Date.now()): number | null {
    const files = this.files();
    const latest = files.at(-1);
    if (!latest) return null;
    return now - statSync(join(this.directory, latest)).mtimeMs;
  }

  async createIfDue(now = Date.now()): Promise<string | null> {
    const age = this.latestAgeMs(now);
    if (age !== null && age < DAY_MS) return null;
    mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    const stamp = new Date(now).toISOString().replaceAll(":", "-");
    const destination = join(this.directory, `mattgpt-${stamp}.sqlite`);
    writeFileSync(destination, this.db.raw.serialize(), { mode: 0o600 });
    const timestamp = new Date(now);
    utimesSync(destination, timestamp, timestamp);
    const files = this.files();
    for (const old of files.slice(0, Math.max(0, files.length - 7)))
      unlinkSync(join(this.directory, old));
    return destination;
  }

  private files(): string[] {
    try {
      return readdirSync(this.directory)
        .filter((name) => /^mattgpt-.*\.sqlite$/.test(name))
        .sort();
    } catch {
      return [];
    }
  }
}
