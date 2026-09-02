import { describe, expect, test } from "bun:test";
import { SlashCommands } from "../src/slack/commands.ts";
import { context, testDatabase } from "./helpers.ts";

describe("slash command skills and emergency model controls", () => {
  test("shows skills while validating and persisting owner-only model changes", async () => {
    const db = testDatabase();
    let model = "openai/gpt-5.6-luna";
    const commands = new SlashCommands(
      db,
      "UOWNER",
      {
        getPrimaryModel: () => model,
        setPrimaryModel: (value) => {
          model = value;
        },
        resolveModel: async (value) => (value === "stealth/ox-alpha" ? "~stealth/ox-alpha" : null),
      },
      "openai/gpt-5.6-luna",
    );
    const other = context({ requesterId: "UOTHER", isOwner: false });
    const owner = context({ requesterId: "UOWNER", isOwner: true });

    expect(await commands.execute("", other)).toContain("MattGPT capabilities");
    expect(await commands.execute("status", other)).toContain("Message me naturally");
    expect(await commands.execute("help", other)).toContain("Read a specific public webpage");
    expect(await commands.execute("help", other)).toContain("Sonarr, Radarr, or SABnzbd");
    expect(await commands.execute("help", other)).toContain("delete selected episode files");
    expect(await commands.execute("model", other)).toBe("Primary model: `openai/gpt-5.6-luna`");
    expect(await commands.execute("model stealth/ox-alpha", other)).toBe(
      "Only the MattGPT owner can change the model.",
    );
    expect(await commands.execute("model stealth/ox-alpha", owner)).toBe(
      "Primary model changed to `~stealth/ox-alpha`.",
    );
    expect(db.getSetting("primary_model")).toBe("~stealth/ox-alpha");
    expect(await commands.execute("model missing/model", owner)).toBe(
      "OpenRouter does not currently list that model; nothing was changed.",
    );
    expect(db.getSetting("primary_model")).toBe("~stealth/ox-alpha");
    expect(await commands.execute("model reset", owner)).toBe(
      "Primary model reset to `openai/gpt-5.6-luna`.",
    );
    expect(db.getSetting("primary_model")).toBeNull();
    db.close();
  });
});
