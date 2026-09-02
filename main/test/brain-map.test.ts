import { afterEach, describe, expect, test } from "bun:test";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildBrainGraph, renderBrainMapSvg, SystemBrainMapRenderer } from "../src/brain/map.ts";

const roots: string[] = [];
const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);

afterEach(() => {
  while (roots.length) {
    const root = roots.pop();
    if (root) rmSync(root, { recursive: true, force: true });
  }
});

function fixtureVault(): string {
  const root = mkdtempSync(join(tmpdir(), "mattgpt-map-vault-"));
  roots.push(root);
  mkdirSync(join(root, "wiki/projects"), { recursive: true });
  mkdirSync(join(root, "wiki/people"), { recursive: true });
  mkdirSync(join(root, "sources/thought"), { recursive: true });
  writeFileSync(
    join(root, "wiki/projects/launch.md"),
    "# Launch Plan\n\nWorks with [[wiki/people/dave]] and [[wiki/people/dave|Dave]].\n[[sources/thought/raw]]\n",
  );
  writeFileSync(
    join(root, "wiki/people/dave.md"),
    '---\ntitle: "Dave & <script>"\n---\n\nBacklink: [[wiki/projects/launch#Status]].\n',
  );
  writeFileSync(join(root, "sources/thought/raw.md"), "# Raw source\n");
  return root;
}

function categoryVault(): string {
  const root = mkdtempSync(join(tmpdir(), "mattgpt-map-categories-"));
  roots.push(root);
  mkdirSync(join(root, "wiki/projects/WoW/characters"), { recursive: true });
  mkdirSync(join(root, "wiki/projects/WoW/planning"), { recursive: true });
  mkdirSync(join(root, "sources/thought"), { recursive: true });
  const note = (title: string, topics: string[], body = "") =>
    `---\ntype: project\ntitle: ${title}\ntopics:\n${topics.map((topic) => `  - ${topic}`).join("\n")}\n---\n\n# ${title}\n\n${body}\n`;
  writeFileSync(
    join(root, "wiki/projects/WoW/wow.md"),
    note(
      "WoW",
      ["world-of-warcraft"],
      "[[wiki/projects/WoW/characters/wow-characters]] [[wiki/projects/WoW/planning/wow-priorities]]",
    ),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/characters/wow-characters.md"),
    note("WoW Characters", ["world-of-warcraft", "characters"]),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/characters/pindruid-balance.md"),
    note("Pindruid Balance", ["world-of-warcraft", "pindruid", "balance-druid"]),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/characters/pindruid-restoration.md"),
    note("Pindruid Restoration", ["world-of-warcraft", "pindruid", "restoration-druid"]),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/characters/pindaladin-retribution.md"),
    note("Pindaladin Retribution", ["world-of-warcraft", "pindaladin", "retribution-paladin"]),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/characters/pindaladin-tank.md"),
    note("Pindaladin Tank Prep", ["world-of-warcraft", "pindaladin", "protection-paladin"]),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/planning/wow-priorities.md"),
    note("WoW Priorities", ["world-of-warcraft", "priorities"]),
  );
  writeFileSync(
    join(root, "wiki/projects/WoW/planning/wow-workflows.md"),
    note("WoW Workflows", ["world-of-warcraft", "workflows"]),
  );
  return root;
}

describe("Brain map export", () => {
  test("maps only real wiki notes and deduplicated wikilinks", () => {
    const graph = buildBrainGraph(fixtureVault(), "general-Matt-Public");

    expect(graph.nodes.map((node) => node.id)).toEqual([
      "wiki/people/dave",
      "wiki/projects/launch",
    ]);
    expect(graph.nodes.map((node) => node.label)).toEqual(["Dave & <script>", "Launch Plan"]);
    expect(graph.edges).toEqual([{ source: "wiki/people/dave", target: "wiki/projects/launch" }]);
    expect(graph.truncated).toBeFalse();
  });

  test("escapes untrusted note titles in deterministic SVG output", () => {
    const svg = renderBrainMapSvg([buildBrainGraph(fixtureVault(), "general-Matt-Public")]);

    expect(svg).toContain("general-Matt-Public");
    expect(svg).toContain("2 notes · 1 relationships");
    expect(svg).toContain("Dave &amp; &lt;script&gt;");
    expect(svg).not.toContain("Dave & <script>");
  });

  test("infers visible categories from hubs, repeated topics, and nested folders", () => {
    const graph = buildBrainGraph(categoryVault(), "ai-model-watch Brain · WoW");
    const groups = Object.fromEntries(graph.nodes.map((node) => [node.label, node.group]));
    expect(groups).toMatchObject({
      WoW: "Index",
      "WoW Characters": "Index",
      "Pindruid Balance": "Pindruid",
      "Pindruid Restoration": "Pindruid",
      "Pindaladin Retribution": "Pindaladin",
      "Pindaladin Tank Prep": "Pindaladin",
      "WoW Priorities": "Planning",
      "WoW Workflows": "Planning",
    });

    const svg = renderBrainMapSvg([graph]);
    expect(svg).toContain(">Index</text>");
    expect(svg).toContain(">PINDRUID</text>");
    expect(svg).toContain(">PINDALADIN</text>");
    expect(svg).toContain(">PLANNING</text>");
    expect(svg).toContain(">Index</text>");
    expect(svg).toContain(">Pindruid</text>");
    expect(svg).toContain("Pindruid Restoration — Pindruid");
  });

  test("renders through a private temporary directory and cleans it after success", () => {
    const parent = mkdtempSync(join(tmpdir(), "mattgpt-map-render-parent-"));
    roots.push(parent);
    const temporary = join(parent, "render");
    const renderer = new SystemBrainMapRenderer(
      (args) => {
        const svgPath = args[1];
        const outputPath = args[2];
        expect(args[0]).toBe("/test/rasterizer");
        expect(svgPath).toBe(join(temporary, "brain-map.svg"));
        expect(outputPath).toBe(join(temporary, "brain-map.png"));
        expect(statSync(temporary).mode & 0o777).toBe(0o700);
        expect(statSync(String(svgPath)).mode & 0o777).toBe(0o600);
        expect(readFileSync(String(svgPath), "utf8")).toContain("MattGPT Brain map");
        writeFileSync(String(outputPath), png);
        return { exitCode: 0, stdout: "", stderr: "" };
      },
      () => {
        mkdirSync(temporary, { mode: 0o700 });
        return temporary;
      },
      (svgPath, outputPath) => ["/test/rasterizer", svgPath, outputPath],
    );

    const exported = renderer.render([buildBrainGraph(fixtureVault(), "Matt-Private")]);

    expect(exported).toMatchObject({
      mediaType: "image/png",
      filename: "brain-map.png",
      title: "Brain map",
      brainCount: 1,
      nodeCount: 2,
      edgeCount: 1,
    });
    expect(exported.bytes).toEqual(png);
    expect(existsSync(temporary)).toBeFalse();
  });

  test("cleans the temporary directory when the renderer fails", () => {
    const parent = mkdtempSync(join(tmpdir(), "mattgpt-map-failure-parent-"));
    roots.push(parent);
    const temporary = join(parent, "render");
    const renderer = new SystemBrainMapRenderer(
      () => ({ exitCode: 1, stdout: "", stderr: "/private/secret/render/path" }),
      () => {
        mkdirSync(temporary, { mode: 0o700 });
        return temporary;
      },
      (svgPath, outputPath) => ["/test/rasterizer", svgPath, outputPath],
    );

    expect(() => renderer.render([buildBrainGraph(fixtureVault(), "Matt-Private")])).toThrow(
      "Brain map rendering failed.",
    );
    expect(existsSync(temporary)).toBeFalse();
  });
});
