import { afterEach, describe, expect, test } from "bun:test";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { BrainGraph, BrainMapExport, BrainMapRenderer } from "../src/brain/map.ts";
import { ScopedBrainRepository } from "../src/brain/scoped.ts";
import type { BrainRunner } from "../src/brain/vault.ts";
import type { RuntimeContext } from "../src/types.ts";
import { context } from "./helpers.ts";

const roots: string[] = [];

afterEach(() => {
  while (roots.length) {
    const root = roots.pop();
    if (root) rmSync(root, { recursive: true, force: true });
  }
});

function ownerVault(): string {
  const root = mkdtempSync(join(tmpdir(), "mattgpt-owner-brain-"));
  roots.push(root);
  for (const path of [
    ".obsidian",
    "sources/thought",
    "wiki/concepts",
    "wiki/people",
    "wiki/projects",
    "wiki/syntheses",
    "maps",
    "scripts",
  ]) {
    mkdirSync(join(root, path), { recursive: true });
  }
  writeFileSync(join(root, "scripts/vault.py"), "# fixture\n");
  writeFileSync(
    join(root, "wiki/projects/owner.md"),
    "# Owner Brain\n\n## Summary\n\nOwner-only legacy knowledge.\n",
  );
  return root;
}

class FakeRunner implements BrainRunner {
  private captures = 0;

  assertClean(): void {}

  capture(root: string, input: { title: string; text: string }) {
    this.captures += 1;
    const path = `sources/thought/capture-${this.captures}.md`;
    writeFileSync(join(root, path), `# ${input.title}\n\n${input.text}\n`, { mode: 0o600 });
    return { source: path, created: true };
  }

  lint(): void {}

  commit(): void {}
}

function dm(userId: string, isOwner = false): RuntimeContext {
  return context({
    workspaceId: "T123",
    requesterId: userId,
    requesterName: isOwner ? "Matt" : "Internal User",
    surface: "dm",
    channelId: `D-${userId}`,
    isOwner,
  });
}

function channel(channelId: string, userId: string): RuntimeContext {
  return context({
    workspaceId: "T123",
    requesterId: userId,
    requesterName: "Internal User",
    surface: "channel",
    channelId,
    isOwner: userId === "UOWNER",
  });
}

function repository(mapRenderer?: BrainMapRenderer) {
  const owner = ownerVault();
  const scopes = mkdtempSync(join(tmpdir(), "mattgpt-scoped-brains-"));
  roots.push(scopes);
  return {
    owner,
    scopes,
    brain: new ScopedBrainRepository(owner, scopes, "UOWNER", "T123", new Set(["C123", "C456"]), {
      runnerFactory: () => new FakeRunner(),
      channelLabels: new Map([
        ["C123", "general"],
        ["C456", "planning"],
      ]),
      ...(mapRenderer ? { mapRenderer } : {}),
    }),
  };
}

class FakeMapRenderer implements BrainMapRenderer {
  readonly calls: BrainGraph[][] = [];

  render(graphs: BrainGraph[]): BrainMapExport {
    this.calls.push(graphs);
    return {
      bytes: Buffer.from("map"),
      mediaType: "image/png",
      filename: "brain-map.png",
      title: "Brain map",
      altText: "Brain map",
      brainCount: graphs.length,
      nodeCount: graphs.reduce((sum, graph) => sum + graph.nodes.length, 0),
      edgeCount: graphs.reduce((sum, graph) => sum + graph.edges.length, 0),
    };
  }
}

describe("scoped Brains", () => {
  test("keeps the existing owner vault as the owner's private DM Brain", () => {
    const { brain, scopes } = repository();
    const result = brain.list({ limit: 20, context: dm("UOWNER", true) }) as {
      brainScope: string;
      total: number;
      notes: Array<{ title: string; brain: string; path: string }>;
    };

    expect(result).toMatchObject({ brainScope: "federated", total: 1 });
    expect(result.notes[0]?.title).toBe("Owner Brain");
    expect(result.notes[0]?.brain).toBe("Matt-Private");
    expect(result.notes[0]?.path).toStartWith("brain-ref:");
    expect(readdirSync(scopes)).toEqual([]);
  });

  test("isolates every DM user and prevents owner or peer access", () => {
    const { brain, scopes } = repository();
    const saved = brain.save({
      destinationKind: "list",
      destinationTitle: "To Do",
      text: "Handle User A confidential material",
      entryKind: "task",
      section: "Private",
      topics: ["private"],
      context: dm("UAAA"),
    }) as { brainScope: string; destination: { title: string } };
    expect(saved).toMatchObject({
      brainScope: "private",
      destination: { title: "To Do" },
    });

    const ownSearch = brain.search({ query: "confidential", limit: 5, context: dm("UAAA") }) as {
      results: unknown[];
    };
    expect(ownSearch.results).toHaveLength(2);
    const peerSearch = brain.search({ query: "confidential", limit: 5, context: dm("UBBB") }) as {
      results: unknown[];
    };
    expect(peerSearch.results).toEqual([]);
    expect(() => brain.read({ path: "wiki/lists/to-do", context: dm("UBBB") })).toThrow(
      "not found",
    );
    const ownerSearch = brain.search({
      query: "confidential",
      limit: 5,
      context: dm("UOWNER", true),
    }) as { results: unknown[] };
    expect(ownerSearch.results).toEqual([]);

    const keys = readdirSync(join(scopes, "users"));
    expect(keys).toHaveLength(2);
    expect(keys.every((key) => /^[a-f0-9]{32}$/.test(key))).toBe(true);
    expect(keys.join(" ")).not.toContain("UAAA");
    expect(keys.join(" ")).not.toContain("UBBB");
    expect(statSync(scopes).mode & 0o777).toBe(0o700);
    for (const key of keys) {
      expect(statSync(join(scopes, "users", key)).mode & 0o777).toBe(0o700);
    }
  });

  test("shares a channel Brain with that channel only", () => {
    const { brain, scopes } = repository();
    const captured = brain.capture({
      title: "Launch Decision",
      text: "The channel selected the blue launch plan.",
      noteType: "project",
      topics: ["launch"],
      context: channel("C123", "UAAA"),
    }) as { brainScope: string };
    expect(captured.brainScope).toBe("channel");

    const teammate = brain.search({
      query: "blue launch",
      limit: 5,
      context: channel("C123", "UBBB"),
    }) as { brainScope: string; results: unknown[] };
    expect(teammate).toMatchObject({ brainScope: "channel" });
    expect(teammate.results).toHaveLength(2);

    const otherChannel = brain.search({
      query: "blue launch",
      limit: 5,
      context: channel("C456", "UBBB"),
    }) as { results: unknown[] };
    expect(otherChannel.results).toEqual([]);
    const keys = readdirSync(join(scopes, "channels"));
    expect(keys).toHaveLength(2);
    expect(keys.join(" ")).not.toContain("C123");
    expect(keys.join(" ")).not.toContain("C456");
  });

  test("exports only the current channel, while owner DM exports public Brains in panels", () => {
    const renderer = new FakeMapRenderer();
    const { brain } = repository(renderer);
    brain.save({
      destinationKind: "topic",
      destinationTitle: "General Plan",
      text: "General channel map note.",
      entryKind: "prose",
      topics: ["general"],
      context: channel("C123", "UOWNER"),
    });
    brain.save({
      destinationKind: "topic",
      destinationTitle: "Planning Plan",
      text: "Planning channel map note.",
      entryKind: "prose",
      topics: ["planning"],
      context: channel("C456", "UOWNER"),
    });
    brain.save({
      destinationKind: "topic",
      destinationTitle: "Someone Else Secret",
      text: "This private note must stay excluded.",
      entryKind: "prose",
      topics: ["secret"],
      context: dm("UAAA"),
    });

    brain.exportMap({ context: channel("C123", "UOWNER") });
    expect(renderer.calls.at(-1)?.map((graph) => graph.label)).toEqual(["general-Matt-Public"]);
    expect(renderer.calls.at(-1)?.[0]?.nodes.map((node) => node.label)).toEqual(["General Plan"]);

    brain.exportMap({ context: dm("UOWNER", true) });
    const federated = renderer.calls.at(-1) ?? [];
    expect(federated.map((graph) => graph.label)).toEqual([
      "Matt-Private",
      "general-Matt-Public",
      "planning-Matt-Public",
    ]);
    expect(federated.flatMap((graph) => graph.nodes.map((node) => node.label))).not.toContain(
      "Someone Else Secret",
    );
  });

  test("federates owner-DM reads across private and approved public Brains only", () => {
    const { brain } = repository();
    const ownerContext = dm("UOWNER", true);
    brain.save({
      destinationKind: "topic",
      destinationTitle: "Roadmap",
      text: "Private roadmap draft.",
      entryKind: "prose",
      topics: ["roadmap"],
      context: ownerContext,
    });
    brain.save({
      destinationKind: "topic",
      destinationTitle: "Roadmap",
      text: "General channel roadmap decision.",
      entryKind: "prose",
      topics: ["roadmap"],
      context: channel("C123", "UOWNER"),
    });
    brain.save({
      destinationKind: "topic",
      destinationTitle: "Planning Notes",
      text: "Planning channel cobalt launch detail.",
      entryKind: "prose",
      topics: ["planning"],
      context: channel("C456", "UOWNER"),
    });

    const listed = brain.list({ limit: 20, context: ownerContext }) as {
      brainScope: string;
      notes: Array<{ path: string; title: string; brain: string }>;
    };
    expect(listed.brainScope).toBe("federated");
    expect(
      listed.notes.filter((note) => note.title === "Roadmap").map((note) => note.brain),
    ).toEqual(["Matt-Private", "general-Matt-Public"]);
    expect(listed.notes.map((note) => note.brain)).toContain("planning-Matt-Public");
    expect(listed.notes.some((note) => note.path.includes("C123"))).toBeFalse();

    const publicRoadmap = listed.notes.find(
      (note) => note.title === "Roadmap" && note.brain === "general-Matt-Public",
    );
    expect(publicRoadmap).toBeDefined();
    const read = brain.read({ path: publicRoadmap?.path ?? "", context: ownerContext }) as {
      brain: string;
      content: string;
      path?: string;
    };
    expect(read).toMatchObject({
      brain: "general-Matt-Public",
      content: expect.stringContaining("General channel roadmap decision"),
    });
    expect(read.path).toBeUndefined();
    expect(() =>
      brain.read({
        path: publicRoadmap?.path ?? "",
        context: { ...ownerContext, turnId: "Ev-later" },
      }),
    ).toThrow("search again");

    const searched = brain.search({
      query: "cobalt launch",
      limit: 5,
      context: ownerContext,
    }) as { results: Array<{ brain: string }> };
    expect(searched.results.some((result) => result.brain === "planning-Matt-Public")).toBeTrue();
    const otherDm = brain.search({ query: "cobalt launch", limit: 5, context: dm("UAAA") }) as {
      results: unknown[];
    };
    expect(otherDm.results).toEqual([]);
  });

  test("keeps owner-DM writes private even when reads are federated", () => {
    const { owner, brain } = repository();
    brain.save({
      destinationKind: "list",
      destinationTitle: "To Do",
      text: "Private owner task",
      entryKind: "task",
      topics: ["private"],
      context: dm("UOWNER", true),
    });

    const channelSearch = brain.search({
      query: "private owner task",
      limit: 5,
      context: channel("C123", "UOWNER"),
    }) as { results: unknown[] };
    expect(channelSearch.results).toEqual([]);
    const ownerSearch = brain.search({
      query: "private owner task",
      limit: 5,
      context: dm("UOWNER", true),
    }) as { results: unknown[] };
    expect(ownerSearch.results.length).toBeGreaterThan(0);
    expect(
      brain.removeListEntry({
        destinationTitle: "To Do",
        text: "Private owner task",
        context: dm("UOWNER", true),
      }),
    ).toMatchObject({ removed: true, brainScope: "private" });
    expect(readFileSync(join(owner, "wiki/lists/to-do.md"), "utf8")).not.toContain(
      "Private owner task",
    );
  });

  test("preserves removed channel Brains on disk but excludes them from owner-DM reads", () => {
    const { owner, scopes, brain } = repository();
    brain.save({
      destinationKind: "topic",
      destinationTitle: "Archived Channel Decision",
      text: "The retired channel selected orange.",
      entryKind: "prose",
      topics: ["archive"],
      context: channel("C456", "UOWNER"),
    });
    const channelsRoot = join(scopes, "channels");
    expect(readdirSync(channelsRoot)).toHaveLength(1);

    const restricted = new ScopedBrainRepository(
      owner,
      scopes,
      "UOWNER",
      "T123",
      new Set(["C123"]),
      {
        runnerFactory: () => new FakeRunner(),
        channelLabels: new Map([["C123", "general"]]),
      },
    );
    const search = restricted.search({
      query: "retired orange",
      limit: 5,
      context: dm("UOWNER", true),
    }) as { results: unknown[] };
    expect(search.results).toEqual([]);
    expect(readdirSync(channelsRoot)).toHaveLength(1);
  });

  test("rejects cross-workspace and unapproved-channel access before creating a vault", () => {
    const { brain, scopes } = repository();
    expect(() =>
      brain.list({ limit: 20, context: { ...dm("UAAA"), workspaceId: "TOTHER" } }),
    ).toThrow("workspace");
    expect(() => brain.list({ limit: 20, context: channel("CNOPE", "UAAA") })).toThrow(
      "approved channels",
    );
    expect(() =>
      brain.exportMap({ context: { ...dm("UOWNER", true), workspaceId: "TOTHER" } }),
    ).toThrow("workspace");
    expect(() => brain.exportMap({ context: channel("CNOPE", "UAAA") })).toThrow(
      "approved channels",
    );
    expect(readdirSync(scopes)).toEqual([]);
  });

  test("initializes scoped vaults without copying owner knowledge", () => {
    const { brain, scopes } = repository();
    brain.list({ limit: 20, context: dm("UAAA") });
    const key = readdirSync(join(scopes, "users"))[0];
    expect(key).toBeDefined();
    const root = join(scopes, "users", key ?? "");
    expect(readFileSync(join(root, "Home.md"), "utf8")).toContain("isolated MattGPT Brain");
    expect(readFileSync(join(root, "Home.md"), "utf8")).not.toContain("Owner-only legacy");
    expect(existsSync(join(root, "wiki/lists"))).toBe(true);
    expect(existsSync(join(root, "wiki/areas"))).toBe(true);
    expect(existsSync(join(root, ".git"))).toBe(true);
  });
});
