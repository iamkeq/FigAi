const REDACTED = "[REDACTED]";
const SECRET_KEY = /(token|secret|authorization|api[-_]?key|cookie|profile|avatar|image_url)/i;
const SECRET_VALUE = /(xox[baprs]-|xapp-|sk-or-v1-)[A-Za-z0-9-_.]+/g;

export function redact(value: unknown): unknown {
  if (typeof value === "string") return value.replaceAll(SECRET_VALUE, REDACTED);
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        SECRET_KEY.test(key) ? REDACTED : redact(item),
      ]),
    );
  }
  return value;
}

export function log(
  level: "debug" | "info" | "warn" | "error",
  event: string,
  metadata: Record<string, unknown> = {},
): void {
  const entry = redact({ timestamp: new Date().toISOString(), level, event, ...metadata });
  const line = JSON.stringify(entry);
  if (level === "error") console.error(line);
  else if (level === "warn") console.warn(line);
  else console.log(line);
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
