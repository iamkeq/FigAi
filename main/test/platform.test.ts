import { describe, expect, test } from "bun:test";
import { join } from "node:path";
import { defaultConfigDir, defaultDataDir, defaultEnvPath } from "../src/platform.ts";

describe("platform paths", () => {
  test("preserves the existing macOS locations", () => {
    expect(defaultConfigDir({}, "darwin", "/Users/matt")).toBe(
      "/Users/matt/Library/Application Support/MattGPT",
    );
    expect(defaultDataDir({}, "darwin", "/Users/matt")).toBe(
      "/Users/matt/Library/Application Support/MattGPT",
    );
  });

  test("uses XDG locations on Linux", () => {
    const env = {
      XDG_CONFIG_HOME: "/srv/config",
      XDG_DATA_HOME: "/srv/data",
    };
    expect(defaultConfigDir(env, "linux", "/home/matt")).toBe("/srv/config/mattgpt");
    expect(defaultDataDir(env, "linux", "/home/matt")).toBe("/srv/data/mattgpt");
  });

  test("falls back to conventional Linux user locations", () => {
    expect(defaultConfigDir({}, "linux", "/home/matt")).toBe("/home/matt/.config/mattgpt");
    expect(defaultDataDir({}, "linux", "/home/matt")).toBe("/home/matt/.local/share/mattgpt");
  });

  test("accepts only an absolute env path override", () => {
    expect(
      defaultEnvPath({ MATTGPT_ENV_PATH: "/run/secrets/mattgpt" }, "linux", "/home/matt"),
    ).toBe("/run/secrets/mattgpt");
    expect(() =>
      defaultEnvPath({ MATTGPT_ENV_PATH: "relative/.env" }, "linux", "/home/matt"),
    ).toThrow("absolute path");
    expect(defaultEnvPath({}, "linux", "/home/matt")).toBe(
      join("/home/matt", ".config", "mattgpt", ".env"),
    );
  });
});
