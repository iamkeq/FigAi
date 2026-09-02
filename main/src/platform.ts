import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join } from "node:path";

export type SupportedPlatform = "darwin" | "linux";

function supportedPlatform(platform: NodeJS.Platform = process.platform): SupportedPlatform {
  if (platform === "darwin" || platform === "linux") return platform;
  throw new Error(`FigAi does not support ${platform}. Use macOS or Linux.`);
}

export function defaultConfigDir(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  home = homedir(),
): string {
  return supportedPlatform(platform) === "darwin"
    ? join(home, "Library", "Application Support", "FigAi")
    : join(env.XDG_CONFIG_HOME || join(home, ".config"), "figai");
}

export function defaultDataDir(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  home = homedir(),
): string {
  return supportedPlatform(platform) === "darwin"
    ? join(home, "Library", "Application Support", "FigAi")
    : join(env.XDG_DATA_HOME || join(home, ".local", "share"), "figai");
}

export function defaultEnvPath(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  home = homedir(),
): string {
  const override = env.FIGAI_ENV_PATH?.trim();
  if (override) {
    if (!isAbsolute(override)) throw new Error("FIGAI_ENV_PATH must be an absolute path.");
    return override;
  }
  return join(defaultConfigDir(env, platform, home), ".env");
}

export function resolveSystemCommand(
  name: string,
  options: {
    which?: (command: string) => string | null;
    candidates?: readonly string[];
  } = {},
): string {
  const which = options.which ?? Bun.which;
  const resolved = which(name);
  if (resolved && isAbsolute(resolved) && existsSync(resolved)) return resolved;
  for (const candidate of options.candidates ?? []) {
    if (isAbsolute(candidate) && existsSync(candidate)) return candidate;
  }
  throw new Error(`Required system command not found: ${name}`);
}
