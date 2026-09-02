import { describe, expect, test } from "bun:test";
import { join } from "node:path";
import { defaultConfigDir, defaultDataDir, defaultEnvPath } from "../src/platform.ts";

describe("platform paths", () => {
  test("preserves the existing macOS locations", () => {
    expect(defaultConfigDir({}, "darwin", "/Users/matt")).toBe(
      "/Users/matt/Library/Application Support/FigAi",
    );
    expect(defaultDataDir({}, "darwin", "/Users/matt")).toBe(
      "/Users/matt/Library/Application Support/FigAi",
    );
  });

  test("uses XDG locations on Linux", () => {
    const env = {
      XDG_CONFIG_HOME: "/srv/config",
      XDG_DATA_HOME: "/srv/data",
    };
    expect(defaultConfigDir(env, "linux", "/home/matt")).toBe("/srv/config/figai");
    expect(defaultDataDir(env, "linux", "/home/matt")).toBe("/srv/data/figai");
  });

  test("falls back to conventional Linux user locations", () => {
    expect(defaultConfigDir({}, "linux", "/home/matt")).toBe("/home/matt/.config/figai");
    expect(defaultDataDir({}, "linux", "/home/matt")).toBe("/home/matt/.local/share/figai");
  });

  test("accepts only an absolute env path override", () => {
    expect(defaultEnvPath({ FIGAI_ENV_PATH: "/run/secrets/figai" }, "linux", "/home/matt")).toBe(
      "/run/secrets/figai",
    );
    expect(() =>
      defaultEnvPath({ FIGAI_ENV_PATH: "relative/.env" }, "linux", "/home/matt"),
    ).toThrow("absolute path");
    expect(defaultEnvPath({}, "linux", "/home/matt")).toBe(
      join("/home/matt", ".config", "figai", ".env"),
    );
  });
});
