import {
  chmodSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, relative, resolve, sep } from "node:path";
import { resolveSystemCommand } from "../platform.ts";
import type { RuntimeContext } from "../types.ts";

const MAX_MAP_NODES = 75;
const MAX_MAP_EDGES = 500;
const MAX_NOTE_BYTES = 1024 * 1024;
const MAX_PNG_BYTES = 10 * 1024 * 1024;

export interface BrainGraphNode {
  id: string;
  label: string;
  group: string;
}

export interface BrainGraphEdge {
  source: string;
  target: string;
}

export interface BrainGraph {
  label: string;
  nodes: BrainGraphNode[];
  edges: BrainGraphEdge[];
  truncated: boolean;
}

export interface BrainMapExport {
  bytes: Buffer;
  mediaType: "image/png";
  filename: "brain-map.png";
  title: "Brain map";
  altText: string;
  brainCount: number;
  nodeCount: number;
  edgeCount: number;
}

export interface BrainMapProvider {
  exportMap(input: { context: RuntimeContext }): BrainMapExport;
}

export interface BrainMapRenderer {
  render(graphs: BrainGraph[]): BrainMapExport;
}

type CommandResult = { exitCode: number; stdout: string; stderr: string };
type MapCommand = (args: string[]) => CommandResult;
type MapRasterizer = (svgPath: string, outputPath: string) => string[];

function defaultCommand(args: string[]): CommandResult {
  const result = Bun.spawnSync({ cmd: args, stdout: "pipe", stderr: "pipe" });
  return {
    exitCode: result.exitCode,
    stdout: result.stdout.toString(),
    stderr: result.stderr.toString(),
  };
}

function optionalSystemCommand(name: string, candidates: readonly string[] = []): string | null {
  try {
    return resolveSystemCommand(name, { candidates });
  } catch {
    return null;
  }
}

export function systemBrainMapRasterizer(
  platform: NodeJS.Platform = process.platform,
): MapRasterizer {
  if (platform === "darwin") {
    const sips = resolveSystemCommand("sips", { candidates: ["/usr/bin/sips"] });
    return (svgPath, outputPath) => [sips, "-s", "format", "png", svgPath, "--out", outputPath];
  }
  if (platform === "linux") {
    const rsvg = optionalSystemCommand("rsvg-convert", ["/usr/bin/rsvg-convert"]);
    if (rsvg) return (svgPath, outputPath) => [rsvg, "-o", outputPath, svgPath];
    const magick = optionalSystemCommand("magick", ["/usr/bin/magick"]);
    if (magick) return (svgPath, outputPath) => [magick, svgPath, outputPath];
    const convert = optionalSystemCommand("convert", ["/usr/bin/convert"]);
    if (convert) return (svgPath, outputPath) => [convert, svgPath, outputPath];
    throw new Error(
      "Brain map export requires rsvg-convert or ImageMagick on Linux (for example: sudo apt install librsvg2-bin).",
    );
  }
  throw new Error(`Brain map export is not supported on ${platform}.`);
}

function markdownTitle(text: string, fallback: string): string {
  const match = text.match(/^title:\s*["']?(.+?)["']?\s*$/m) ?? text.match(/^#\s+(.+?)\s*$/m);
  return match?.[1]?.trim() || fallback;
}

function normalizeWikiTarget(value: string): string {
  return value.trim().replaceAll("\\", "/").replace(/\.md$/i, "");
}

function frontmatter(text: string): string {
  if (!text.startsWith("---\n")) return "";
  const end = text.indexOf("\n---\n", 4);
  return end < 0 ? "" : text.slice(4, end);
}

function yamlScalar(value: string): string {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1).trim();
  }
  return trimmed;
}

function frontmatterValue(text: string, keys: string[]): string | undefined {
  const yaml = frontmatter(text);
  for (const key of keys) {
    const match = yaml.match(new RegExp(`^${key}:\\s*(.+?)\\s*$`, "mi"));
    const value = match?.[1] ? yamlScalar(match[1]) : "";
    if (value) return value;
  }
  return undefined;
}

function frontmatterList(text: string, key: string): string[] {
  const yaml = frontmatter(text);
  const lines = yaml.split(/\r?\n/);
  const start = lines.findIndex((line) => new RegExp(`^${key}:\\s*$`, "i").test(line));
  if (start < 0) return [];
  const values: string[] = [];
  for (const line of lines.slice(start + 1)) {
    const match = line.match(/^\s+-\s+(.+?)\s*$/);
    if (!match) break;
    const value = yamlScalar(match[1] ?? "");
    if (value) values.push(value);
  }
  return values;
}

function normalizedGroup(value: string): string {
  return value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function groupLabel(value: string): string {
  return value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => `${part[0]?.toUpperCase() ?? ""}${part.slice(1)}`)
    .join(" ");
}

interface GraphRecord {
  node: BrainGraphNode;
  text: string;
  segments: string[];
  topics: string[];
  explicitGroup?: string;
}

function isIndexRecord(record: GraphRecord): boolean {
  const collection = record.segments[2];
  const filename = record.segments.at(-1);
  if (!collection || !filename) return false;
  if (normalizedGroup(filename) === normalizedGroup(collection)) return true;
  const subsection = record.segments.length >= 5 ? record.segments[3] : undefined;
  return Boolean(
    subsection &&
      normalizedGroup(record.node.label) === normalizedGroup(`${collection} ${subsection}`),
  );
}

function assignGroups(records: GraphRecord[]): void {
  const topicCounts = new Map<string, { count: number; label: string }>();
  for (const record of records) {
    for (const topic of new Set(record.topics.map(normalizedGroup).filter(Boolean))) {
      const current = topicCounts.get(topic);
      topicCounts.set(topic, {
        count: (current?.count ?? 0) + 1,
        label: current?.label ?? groupLabel(topic),
      });
    }
  }
  for (const record of records) {
    if (record.explicitGroup) {
      record.node.group = groupLabel(record.explicitGroup);
      continue;
    }
    if (isIndexRecord(record)) {
      record.node.group = "Index";
      continue;
    }
    const repeatedTopic = record.topics
      .map((topic) => ({
        topic: normalizedGroup(topic),
        ...topicCounts.get(normalizedGroup(topic)),
      }))
      .filter(
        (candidate): candidate is { topic: string; count: number; label: string } =>
          Boolean(candidate.topic) &&
          typeof candidate.count === "number" &&
          candidate.count >= 2 &&
          candidate.count < records.length,
      )
      .sort(
        (left, right) =>
          left.count - right.count ||
          right.topic.length - left.topic.length ||
          left.topic.localeCompare(right.topic),
      )[0];
    if (repeatedTopic) {
      record.node.group = repeatedTopic.label;
      continue;
    }
    const nestedFolder = record.segments.length >= 5 ? record.segments.at(-2) : undefined;
    record.node.group = groupLabel(nestedFolder ?? record.segments[1] ?? "Wiki");
  }
}

export function buildBrainGraph(root: string, label: string): BrainGraph {
  const vaultRoot = realpathSync(resolve(root));
  const wikiRoot = realpathSync(join(vaultRoot, "wiki"));
  const records: GraphRecord[] = [];
  const entries = [...new Bun.Glob("**/*.md").scanSync({ cwd: wikiRoot, onlyFiles: true })].sort();
  let truncated = entries.length > MAX_MAP_NODES;
  for (const entry of entries.slice(0, MAX_MAP_NODES)) {
    const unresolved = join(wikiRoot, entry);
    const absolute = realpathSync(unresolved);
    if (!absolute.startsWith(`${wikiRoot}${sep}`) || !statSync(absolute).isFile()) continue;
    if (statSync(absolute).size > MAX_NOTE_BYTES) continue;
    const text = readFileSync(absolute, "utf8");
    const path = relative(vaultRoot, absolute).replaceAll(sep, "/").replace(/\.md$/i, "");
    const explicitGroup = frontmatterValue(text, ["map_group", "category"]);
    records.push({
      node: {
        id: path,
        label: markdownTitle(text, basename(path)),
        group: "Wiki",
      },
      text,
      segments: path.split("/"),
      topics: frontmatterList(text, "topics"),
      ...(explicitGroup ? { explicitGroup } : {}),
    });
  }
  records.sort((left, right) => left.node.id.localeCompare(right.node.id));
  assignGroups(records);
  const nodeIds = new Set(records.map((record) => record.node.id));
  const edgeKeys = new Set<string>();
  const edges: BrainGraphEdge[] = [];
  for (const record of records) {
    const links = record.text.matchAll(/\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]/g);
    for (const link of links) {
      const target = normalizeWikiTarget(link[1] ?? "");
      if (!nodeIds.has(target) || target === record.node.id) continue;
      const [sourceId, targetId] = [record.node.id, target].sort();
      const key = `${sourceId}\0${targetId}`;
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({ source: sourceId ?? record.node.id, target: targetId ?? target });
      if (edges.length >= MAX_MAP_EDGES) {
        truncated = true;
        break;
      }
    }
    if (edges.length >= MAX_MAP_EDGES) break;
  }
  edges.sort(
    (left, right) =>
      left.source.localeCompare(right.source) || left.target.localeCompare(right.target),
  );
  return { label, nodes: records.map((record) => record.node), edges, truncated };
}

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function truncateLabel(value: string, maximum = 28): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length <= maximum ? compact : `${compact.slice(0, maximum - 1).trimEnd()}…`;
}

function colorForGroup(group: string): string {
  if (group === "Index") return "#70b7ff";
  const colors = ["#70b7ff", "#ff9d57", "#63d58a", "#ed83bd", "#b59cff", "#f0c75e"];
  let hash = 0;
  for (const character of group) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length] ?? colors[0] ?? "#70b7ff";
}

function colorsForGroups(groups: string[]): Map<string, string> {
  const palette = ["#70b7ff", "#ff9d57", "#63d58a", "#ed83bd", "#b59cff", "#f0c75e"];
  const assigned = new Map<string, string>();
  const used = new Set<number>();
  if (groups.includes("Index")) {
    assigned.set("Index", palette[0] as string);
    used.add(0);
  }
  for (const group of groups.filter((item) => item !== "Index")) {
    let start = palette.indexOf(colorForGroup(group));
    if (start <= 0) start = 1;
    let selected = start;
    for (let offset = 0; offset < palette.length - 1; offset += 1) {
      const candidate = 1 + ((start - 1 + offset) % (palette.length - 1));
      if (!used.has(candidate)) {
        selected = candidate;
        break;
      }
    }
    assigned.set(group, palette[selected] ?? palette[1] ?? "#ff9d57");
    used.add(selected);
  }
  return assigned;
}

interface PositionedNode extends BrainGraphNode {
  x: number;
  y: number;
  radius: number;
  degree: number;
  primary: boolean;
}

interface PositionedGroup {
  label: string;
  x: number;
  y: number;
}

interface PositionedGraph {
  nodes: PositionedNode[];
  groups: PositionedGroup[];
}

function orderedGroups(nodes: BrainGraphNode[]): string[] {
  return [...new Set(nodes.map((node) => node.group))].sort((left, right) => {
    if (left === "Index") return -1;
    if (right === "Index") return 1;
    return left.localeCompare(right);
  });
}

function positionNodes(graph: BrainGraph, width: number, height: number): PositionedGraph {
  const degree = new Map(graph.nodes.map((node) => [node.id, 0]));
  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const ordered = graph.nodes
    .slice()
    .sort(
      (left, right) =>
        (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0) ||
        left.label.localeCompare(right.label),
    );
  const radius = ordered.length <= 12 ? 50 : ordered.length <= 25 ? 32 : 23;
  const centerX = width / 2;
  const centerY = height / 2 + 12;
  if (!ordered.length) return { nodes: [], groups: [] };
  const primaryId = ordered[0]?.id;
  const groups = orderedGroups(ordered);
  const hasIndex = groups[0] === "Index";
  const outerGroups = hasIndex ? groups.slice(1) : groups;
  const groupCenters = new Map<string, { x: number; y: number }>();
  if (hasIndex) groupCenters.set("Index", { x: centerX, y: centerY });
  for (const [index, group] of outerGroups.entries()) {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / Math.max(outerGroups.length, 1);
    groupCenters.set(group, {
      x: centerX + Math.cos(angle) * width * (hasIndex ? 0.3 : 0.26),
      y: centerY + Math.sin(angle) * height * (hasIndex ? 0.28 : 0.24),
    });
  }
  const positioned: PositionedNode[] = [];
  const positionedGroups: PositionedGroup[] = [];
  for (const group of groups) {
    const center = groupCenters.get(group) ?? { x: centerX, y: centerY };
    const members = ordered.filter((node) => node.group === group);
    const clusterRadius = Math.min(105, Math.max(radius * 1.35, members.length * radius * 0.55));
    for (const [index, node] of members.entries()) {
      const radialAngle = Math.atan2(center.y - centerY, center.x - centerX);
      const angle =
        members.length === 2
          ? (group === "Index" ? 0 : radialAngle + Math.PI / 2) + index * Math.PI
          : -Math.PI / 2 + (index * Math.PI * 2) / Math.max(members.length, 1);
      const spread = members.length === 1 ? 0 : clusterRadius;
      positioned.push({
        ...node,
        x: center.x + Math.cos(angle) * spread,
        y: center.y + Math.sin(angle) * spread,
        radius: node.id === primaryId ? radius + 8 : radius,
        degree: degree.get(node.id) ?? 0,
        primary: node.id === primaryId,
      });
    }
    positionedGroups.push({
      label: group,
      x: center.x,
      y: Math.max(18, center.y - clusterRadius - radius - 20),
    });
  }
  return { nodes: positioned, groups: positionedGroups };
}

function labelLines(value: string): string[] {
  const compact = value.replace(/\s+/g, " ").trim();
  if (compact.length <= 14) return [compact];
  const words = compact.split(" ");
  if (words.length === 1) return [truncateLabel(compact, 18)];
  let split = 1;
  let difference = Number.POSITIVE_INFINITY;
  for (let index = 1; index < words.length; index += 1) {
    const left = words.slice(0, index).join(" ");
    const right = words.slice(index).join(" ");
    const candidate = Math.abs(left.length - right.length);
    if (candidate < difference) {
      difference = candidate;
      split = index;
    }
  }
  return [words.slice(0, split).join(" "), words.slice(split).join(" ")].map((line) =>
    truncateLabel(line, 18),
  );
}

export function renderBrainMapSvg(graphs: BrainGraph[]): string {
  const selected = graphs.filter((graph) => graph.nodes.length > 0);
  if (!selected.length) throw new Error("The accessible Brain has no wiki notes to map.");
  const columns = selected.length > 1 ? 2 : 1;
  const rows = Math.ceil(selected.length / columns);
  const width = 1600;
  const panelWidth = columns === 1 ? 1520 : 750;
  const panelHeight = selected.length === 1 ? 820 : 650;
  const height = 80 + rows * (panelHeight + 30);
  const panels: string[] = [];
  selected.forEach((graph, graphIndex) => {
    const column = graphIndex % columns;
    const row = Math.floor(graphIndex / columns);
    const panelX = 25 + column * (panelWidth + 25);
    const panelY = 70 + row * (panelHeight + 30);
    const innerWidth = panelWidth - 40;
    const innerHeight = panelHeight - 135;
    const layout = positionNodes(graph, innerWidth, innerHeight);
    const nodes = layout.nodes;
    const groupColors = colorsForGroups(orderedGroups(graph.nodes));
    const groupColor = (group: string) => groupColors.get(group) ?? "#70b7ff";
    const byId = new Map(nodes.map((node) => [node.id, node]));
    const edges = graph.edges
      .map((edge) => {
        const source = byId.get(edge.source);
        const target = byId.get(edge.target);
        if (!source || !target) return "";
        const sameGroup = source.group === target.group;
        const stroke = sameGroup ? groupColor(source.group) : "#8d94a4";
        return `<line x1="${source.x.toFixed(1)}" y1="${source.y.toFixed(1)}" x2="${target.x.toFixed(1)}" y2="${target.y.toFixed(1)}" stroke="${stroke}" stroke-opacity="${sameGroup ? "0.55" : "0.35"}" stroke-width="2" />`;
      })
      .join("");
    const nodeMarkup = nodes
      .map((node) => {
        const color = groupColor(node.group);
        const fill = node.primary && node.group === "Index" ? color : "#171a21";
        const textColor = node.primary && node.group === "Index" ? "#10151d" : "#f4f6fa";
        const fontSize = node.radius >= 55 ? 13 : node.radius >= 34 ? 11 : 9;
        const lines = labelLines(node.label);
        const lineHeight = fontSize + 4;
        const firstY = lines.length === 1 ? 4 : -lineHeight / 2 + 3;
        const labels = lines
          .map(
            (line, index) =>
              `<text x="0" y="${(firstY + index * lineHeight).toFixed(1)}" text-anchor="middle" fill="${textColor}" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="${fontSize}" font-weight="600">${escapeXml(line)}</text>`,
          )
          .join("");
        return `<g transform="translate(${node.x.toFixed(1)} ${node.y.toFixed(1)})"><circle r="${node.radius}" fill="${fill}" stroke="${color}" stroke-width="4" /><title>${escapeXml(node.label)} — ${escapeXml(node.group)} · ${node.degree} connections</title>${labels}</g>`;
      })
      .join("");
    const categoryMarkup = layout.groups
      .filter((group) => group.label !== "Index")
      .map(
        (group) =>
          `<text x="${group.x.toFixed(1)}" y="${group.y.toFixed(1)}" text-anchor="middle" fill="${groupColor(group.label)}" fill-opacity="0.92" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="13" font-weight="700" letter-spacing="2">${escapeXml(group.label.toUpperCase())}</text>`,
      )
      .join("");
    const legendGroups = orderedGroups(graph.nodes);
    const legendWidths = legendGroups.map((group) => 34 + Math.min(group.length, 18) * 8);
    const legendTotal = legendWidths.reduce((sum, item) => sum + item, 0);
    let legendX = Math.max(24, (panelWidth - legendTotal) / 2);
    const legendMarkup = legendGroups
      .map((group, index) => {
        const item = `<g transform="translate(${legendX.toFixed(1)} ${panelHeight - 24})"><circle cx="6" cy="-5" r="6" fill="#171a21" stroke="${groupColor(group)}" stroke-width="3" /><text x="20" y="0" fill="#c4c9d4" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="13">${escapeXml(truncateLabel(group, 18))}</text></g>`;
        legendX += legendWidths[index] ?? 0;
        return item;
      })
      .join("");
    panels.push(
      `<g transform="translate(${panelX} ${panelY})"><rect width="${panelWidth}" height="${panelHeight}" rx="20" fill="#20232b" stroke="#3a3f4b" stroke-width="2" /><text x="24" y="38" fill="#f4f6fa" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="22" font-weight="600">${escapeXml(truncateLabel(graph.label, 48))}</text><text x="${panelWidth - 24}" y="38" text-anchor="end" fill="#aab0be" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="15">${graph.nodes.length} notes · ${graph.edges.length} relationships${graph.truncated ? " · capped" : ""}</text><g transform="translate(20 60)">${edges}${categoryMarkup}${nodeMarkup}</g>${legendMarkup}</g>`,
    );
  });
  const totalNodes = selected.reduce((sum, graph) => sum + graph.nodes.length, 0);
  const totalEdges = selected.reduce((sum, graph) => sum + graph.edges.length, 0);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="#12141a" /><text x="28" y="42" fill="#f4f6fa" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="26" font-weight="600">MattGPT Brain map</text><text x="1572" y="42" text-anchor="end" fill="#aab0be" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="16">${selected.length} Brain${selected.length === 1 ? "" : "s"} · ${totalNodes} notes · ${totalEdges} relationships</text>${panels.join("")}</svg>`;
}

function isPng(bytes: Buffer): boolean {
  return (
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47 &&
    bytes[4] === 0x0d &&
    bytes[5] === 0x0a &&
    bytes[6] === 0x1a &&
    bytes[7] === 0x0a
  );
}

export class SystemBrainMapRenderer implements BrainMapRenderer {
  constructor(
    private readonly command: MapCommand = defaultCommand,
    private readonly createTemporaryDirectory: () => string = () =>
      mkdtempSync(join(tmpdir(), "mattgpt-brain-map-")),
    private readonly rasterizer?: MapRasterizer,
  ) {}

  render(graphs: BrainGraph[]): BrainMapExport {
    const svg = renderBrainMapSvg(graphs);
    const temporary = this.createTemporaryDirectory();
    try {
      chmodSync(temporary, 0o700);
      const svgPath = join(temporary, "brain-map.svg");
      const outputPath = join(temporary, "brain-map.png");
      writeFileSync(svgPath, svg, { mode: 0o600 });
      const rasterizer = this.rasterizer ?? systemBrainMapRasterizer();
      const result = this.command(rasterizer(svgPath, outputPath));
      if (result.exitCode !== 0) {
        throw new Error("Brain map rendering failed.");
      }
      if (!existsSync(outputPath)) throw new Error("Brain map rendering did not produce a PNG.");
      const pngPath = realpathSync(outputPath);
      if (!pngPath.startsWith(`${realpathSync(temporary)}${sep}`) || !statSync(pngPath).isFile()) {
        throw new Error("Brain map rendering produced an invalid file.");
      }
      chmodSync(pngPath, 0o600);
      const size = statSync(pngPath).size;
      if (size <= 0 || size > MAX_PNG_BYTES) {
        throw new Error("The rendered Brain map exceeds the 10 MB image limit.");
      }
      const bytes = readFileSync(pngPath);
      if (!isPng(bytes)) throw new Error("Brain map rendering produced an invalid PNG.");
      const selected = graphs.filter((graph) => graph.nodes.length > 0);
      const nodeCount = selected.reduce((sum, graph) => sum + graph.nodes.length, 0);
      const edgeCount = selected.reduce((sum, graph) => sum + graph.edges.length, 0);
      return {
        bytes,
        mediaType: "image/png",
        filename: "brain-map.png",
        title: "Brain map",
        altText: `Brain map with ${nodeCount} notes and ${edgeCount} relationships across ${selected.length} Brain${selected.length === 1 ? "" : "s"}.`,
        brainCount: selected.length,
        nodeCount,
        edgeCount,
      };
    } finally {
      if (existsSync(temporary)) rmSync(temporary, { recursive: true, force: true });
    }
  }
}
