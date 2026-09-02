import { describe, expect, test } from "bun:test";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig, parseConfig } from "../src/config.ts";

const valid = {
  SLACK_BOT_TOKEN: "xoxb-example",
  SLACK_APP_TOKEN: "xapp-example",
  OPENROUTER_API_KEY: "sk-or-v1-example",
  OWNER_USER_ID: "U123ABC",
  ALLOWED_CHANNEL_IDS: "C123ABC,G456DEF,C123ABC",
};

describe("configuration", () => {
  test("parses defaults and deduplicates channels", () => {
    const config = parseConfig(valid);
    expect(config.primaryModel).toBe("openai/gpt-5.6-luna");
    expect(config.fallbackModel).toBe("google/gemini-3.7-flash");
    expect(config.loadingStatusModel).toBe("google/gemini-2.5-flash-lite");
    expect(config.directivePolicyModel).toBe("openai/gpt-5.6-luna");
    expect(config.imageGenerationModel).toBe("google/gemini-3.1-flash-lite-image");
    expect(config.brainVaultPath).toBeNull();
    expect(config.mediaConnections).toEqual({ sonarr: null, radarr: null, sabnzbd: null });
    expect([...config.allowedChannelIds]).toEqual(["C123ABC", "G456DEF"]);
  });

  test("normalizes optional local media service pairs", () => {
    expect(
      parseConfig({
        ...valid,
        SONARR_URL: "http://127.0.0.1:8989/sonarr/",
        SONARR_API_KEY: "sonarr-secret",
        RADARR_URL: "https://media.example.test/radarr",
        RADARR_API_KEY: "radarr-secret",
        SABNZBD_URL: "http://localhost:8080",
        SABNZBD_API_KEY: "sab-secret",
      }).mediaConnections,
    ).toEqual({
      sonarr: { baseUrl: "http://127.0.0.1:8989/sonarr", apiKey: "sonarr-secret" },
      radarr: { baseUrl: "https://media.example.test/radarr", apiKey: "radarr-secret" },
      sabnzbd: { baseUrl: "http://localhost:8080", apiKey: "sab-secret" },
    });
  });

  test("requires complete media pairs and safe base URLs", () => {
    expect(() => parseConfig({ ...valid, SONARR_URL: "http://localhost:8989" })).toThrow(
      "URL and API key must be configured together",
    );
    expect(() =>
      parseConfig({
        ...valid,
        RADARR_URL: "http://user:pass@localhost:7878",
        RADARR_API_KEY: "secret",
      }),
    ).toThrow("without credentials");
    expect(() =>
      parseConfig({
        ...valid,
        SABNZBD_URL: "file:///tmp/sab",
        SABNZBD_API_KEY: "secret",
      }),
    ).toThrow("HTTP(S)");
  });

  test("resolves an optional Obsidian Brain vault path", () => {
    expect(parseConfig({ ...valid, OBSIDIAN_VAULT_PATH: "./brain" }).brainVaultPath).toBe(
      join(process.cwd(), "brain"),
    );
  });

  test("rejects missing required values", () => {
    expect(() => parseConfig({})).toThrow("Invalid MattGPT configuration");
  });

  test("requires the external env file to be mode 0600", () => {
    const directory = mkdtempSync(join(tmpdir(), "mattgpt-config-"));
    const path = join(directory, ".env");
    writeFileSync(
      path,
      Object.entries(valid)
        .map(([key, value]) => `${key}=${value}`)
        .join("\n"),
    );
    chmodSync(path, 0o644);
    expect(() => loadConfig(path)).toThrow("mode 0600");
    chmodSync(path, 0o600);
    expect(loadConfig(path).ownerUserId).toBe("U123ABC");
  });
});
