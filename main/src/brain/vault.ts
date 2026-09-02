import { randomUUID } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { resolveSystemCommand } from "../platform.ts";
import type { RuntimeContext } from "../types.ts";
import {
  type BrainGraph,
  type BrainMapExport,
  type BrainMapProvider,
  type BrainMapRenderer,
  buildBrainGraph,
  SystemBrainMapRenderer,
} from "./map.ts";

const MAX_NOTE_BYTES = 1024 * 1024;
const MAX_READ_CHARACTERS = 20_000;
const MAX_CAPTURE_CHARACTERS = 20_000;
const MAX_SEARCH_FILES = 5_000;
const SEARCH_DIRECTORIES = ["wiki", "sources"] as const;
const READ_DIRECTORIES = ["wiki", "sources", "maps"] as const;
const SEARCH_STOP_WORDS = new Set([
  "a",
  "an",
  "and",
  "are",
  "do",
  "does",
  "for",
  "from",
  "i",
  "in",
  "is",
  "it",
  "me",
  "my",
  "need",
  "of",
  "on",
  "the",
  "to",
  "what",
  "whats",
  "which",
  "who",
  "with",
]);
const NOTE_TYPES = ["concept", "person", "project", "synthesis"] as const;
const TYPE_DIRECTORIES = {
  concept: "concepts",
  person: "people",
  project: "projects",
  synthesis: "syntheses",
} as const;

export type BrainNoteType = (typeof NOTE_TYPES)[number];

const DESTINATION_KINDS = [
  "area",
  "inbox",
  "list",
  "person",
  "project",
  "reference",
  "synthesis",
  "topic",
] as const;
const ENTRY_KINDS = ["fact", "list-item", "prose", "task"] as const;
const DESTINATION_CONFIG = {
  area: { directory: "areas", noteType: "area" },
  inbox: { directory: "inbox", noteType: "inbox" },
  list: { directory: "lists", noteType: "list" },
  person: { directory: "people", noteType: "person" },
  project: { directory: "projects", noteType: "project" },
  reference: { directory: "references", noteType: "reference" },
  synthesis: { directory: "syntheses", noteType: "synthesis" },
  topic: { directory: "concepts", noteType: "concept" },
} as const;

export type BrainDestinationKind = (typeof DESTINATION_KINDS)[number];
export type BrainEntryKind = (typeof ENTRY_KINDS)[number];

export type BrainAccess =
  | { kind: "owner"; ownerUserId: string; workspaceId?: string }
  | { kind: "user"; userId: string; workspaceId: string }
  | { kind: "channel"; channelId: string; workspaceId: string };

export interface BrainRepository {
  list(input: { limit: number; context: RuntimeContext }): unknown;
  search(input: { query: string; limit: number; context: RuntimeContext }): unknown;
  read(input: { path: string; context: RuntimeContext }): unknown;
  save(input: {
    destinationKind: BrainDestinationKind;
    destinationTitle: string;
    text: string;
    entryKind: BrainEntryKind;
    section?: string;
    category?: string;
    topics: string[];
    context: RuntimeContext;
  }): unknown;
  removeListEntry(input: {
    destinationTitle: string;
    text: string;
    context: RuntimeContext;
  }): unknown;
  capture(input: {
    title: string;
    text: string;
    noteType: BrainNoteType;
    topics: string[];
    context: RuntimeContext;
  }): unknown;
  append(input: { path: string; text: string; context: RuntimeContext }): unknown;
}

interface CaptureResult {
  source: string;
  created: boolean;
}

export interface BrainRunner {
  assertClean(root: string): void;
  capture(root: string, input: { title: string; text: string }): CaptureResult;
  lint(root: string): void;
  commit(root: string, message: string, wikiPath: string): void;
}

function command(root: string, args: string[]): string {
  const result = Bun.spawnSync({
    cmd: args,
    cwd: root,
    env: process.env,
    stdout: "pipe",
    stderr: "pipe",
  });
  const stdout = result.stdout.toString().trim();
  const stderr = result.stderr.toString().trim();
  if (result.exitCode !== 0) {
    throw new Error((stderr || stdout || "Brain command failed.").slice(0, 1000));
  }
  return stdout;
}

export class SystemBrainRunner implements BrainRunner {
  private readonly git = resolveSystemCommand("git", {
    candidates: ["/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git"],
  });
  private readonly python = resolveSystemCommand("python3", {
    candidates: ["/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"],
  });

  assertClean(root: string): void {
    const status = command(root, [this.git, "status", "--porcelain", "--untracked-files=all"]);
    if (status) throw new Error("The Brain vault has uncommitted changes; capture is paused.");
  }

  capture(root: string, input: { title: string; text: string }): CaptureResult {
    const output = command(root, [
      this.python,
      "scripts/vault.py",
      "capture",
      "--kind",
      "thought",
      "--title",
      input.title,
      "--text",
      input.text,
    ]);
    const parsed = JSON.parse(output) as Partial<CaptureResult>;
    if (typeof parsed.source !== "string" || typeof parsed.created !== "boolean") {
      throw new Error("The Brain capture script returned an invalid result.");
    }
    return { source: parsed.source, created: parsed.created };
  }

  lint(root: string): void {
    command(root, [this.python, "scripts/vault.py", "lint"]);
  }

  commit(root: string, message: string, wikiPath: string): void {
    command(root, [this.git, "add", "--", "sources", wikiPath, "system/logs", "system/reports"]);
    command(root, [this.git, "commit", "-m", message]);
  }
}

function cleanText(value: string, maxLength: number): string {
  const cleaned = [...value]
    .map((character) => {
      const code = character.codePointAt(0) ?? 0;
      return code === 0 || code === 127 ? " " : character;
    })
    .join("")
    .trim();
  if (!cleaned || cleaned.length > maxLength) {
    throw new Error(`Brain text must be 1–${maxLength.toLocaleString()} characters.`);
  }
  return cleaned;
}

function yamlQuote(value: string): string {
  return JSON.stringify(value);
}

function slugify(value: string): string {
  return (
    value
      .normalize("NFKD")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 72) || "untitled"
  );
}

function sourceLink(path: string): string {
  return `[[${path.replace(/\.md$/i, "")}]]`;
}

function markdownTitle(text: string, fallback: string): string {
  const match = text.match(/^title:\s*["']?(.+?)["']?\s*$/m) ?? text.match(/^#\s+(.+?)\s*$/m);
  return match?.[1]?.trim() || fallback;
}

function normalizeSearchText(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function normalizedListEntry(value: string): string {
  return value.normalize("NFKC").toLocaleLowerCase("en-US").replace(/\s+/g, " ").trim();
}

function removeExactListEntry(markdown: string, requestedText: string): string {
  const frontmatterEnd = markdown.startsWith("---\n") ? markdown.indexOf("\n---", 4) : -1;
  const bodyStart = frontmatterEnd < 0 ? 0 : frontmatterEnd + 4;
  const prefix = markdown.slice(0, bodyStart);
  const lines = markdown.slice(bodyStart).split(/\r?\n/);
  const wanted = normalizedListEntry(requestedText);
  const matches: number[] = [];
  for (const [index, line] of lines.entries()) {
    const match = line.match(/^\s*[-*]\s+(?:\[[ xX]\]\s*)?(.+?)\s*$/);
    if (match?.[1] && normalizedListEntry(match[1]) === wanted) matches.push(index);
  }
  if (matches.length === 0) throw new Error("That exact item was not found in the Brain list.");
  if (matches.length > 1) {
    throw new Error("That list contains multiple matching items; make the target more specific.");
  }
  lines.splice(matches[0] as number, 1);
  const body = lines.join("\n").replace(/\n{3,}/g, "\n\n");
  return `${`${prefix}${body}`.trimEnd()}\n`;
}

function searchTokens(value: string): string[] {
  const tokens = normalizeSearchText(value).split(/\s+/).filter(Boolean);
  const meaningful = tokens.filter((token) => !SEARCH_STOP_WORDS.has(token));
  return [...new Set(meaningful.length ? meaningful : tokens)];
}

function yamlScalar(value: string): string {
  const scalar = value.trim();
  if (
    (scalar.startsWith('"') && scalar.endsWith('"')) ||
    (scalar.startsWith("'") && scalar.endsWith("'"))
  ) {
    if (scalar.startsWith('"')) {
      try {
        const parsed: unknown = JSON.parse(scalar);
        if (typeof parsed === "string") return parsed;
      } catch {
        // Fall back to trimming quotes for permissive YAML frontmatter.
      }
    }
    return scalar.slice(1, -1);
  }
  return scalar;
}

function markdownAliases(text: string): string[] {
  if (!text.startsWith("---\n")) return [];
  const end = text.indexOf("\n---\n", 4);
  if (end < 0) return [];
  const lines = text.slice(4, end).split("\n");
  const aliasIndex = lines.findIndex((line) => /^aliases\s*:/.test(line));
  if (aliasIndex < 0) return [];
  const inline = lines[aliasIndex]?.replace(/^aliases\s*:\s*/, "").trim() ?? "";
  if (inline && inline !== "[]") {
    const value = inline.startsWith("[") && inline.endsWith("]") ? inline.slice(1, -1) : inline;
    return value.split(",").map(yamlScalar).filter(Boolean);
  }
  const aliases: string[] = [];
  for (let index = aliasIndex + 1; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const match = line.match(/^\s+-\s+(.+?)\s*$/);
    if (!match) break;
    const alias = yamlScalar(match[1] ?? "");
    if (alias) aliases.push(alias);
  }
  return aliases;
}

function markdownBody(text: string): string {
  if (!text.startsWith("---\n")) return text;
  const end = text.indexOf("\n---\n", 4);
  return end < 0 ? text : text.slice(end + 5);
}

function editDistanceAtMostOne(left: string, right: string): boolean {
  if (Math.abs(left.length - right.length) > 1) return false;
  let leftIndex = 0;
  let rightIndex = 0;
  let edits = 0;
  while (leftIndex < left.length && rightIndex < right.length) {
    if (left[leftIndex] === right[rightIndex]) {
      leftIndex += 1;
      rightIndex += 1;
      continue;
    }
    edits += 1;
    if (edits > 1) return false;
    if (left.length > right.length) leftIndex += 1;
    else if (right.length > left.length) rightIndex += 1;
    else {
      leftIndex += 1;
      rightIndex += 1;
    }
  }
  return edits + (leftIndex < left.length || rightIndex < right.length ? 1 : 0) <= 1;
}

function fuzzyTokenMatch(query: string, candidate: string): boolean {
  if (query.length < 4 || candidate.length < 4 || query === candidate) return false;
  if (candidate.startsWith(query)) return true;
  if (
    query.endsWith("s") &&
    candidate.length >= query.length + 2 &&
    candidate.startsWith(query.slice(0, -1))
  )
    return true;
  return (
    Math.abs(query.length - candidate.length) <= 1 &&
    query.slice(0, 3) === candidate.slice(0, 3) &&
    editDistanceAtMostOne(query, candidate)
  );
}

type MatchType = "exact" | "alias" | "token" | "fuzzy";

interface SearchField {
  type: "title" | "path" | "alias" | "body";
  phrase: string;
  tokens: string[];
}

interface TokenMatch {
  type: MatchType;
  score: number;
  candidate: string;
}

function matchToken(queryToken: string, fields: SearchField[]): TokenMatch | null {
  const exactWeights = { title: 120, path: 90, alias: 160, body: 40 } as const;
  const fuzzyWeights = { title: 25, path: 18, alias: 30, body: 8 } as const;
  let best: TokenMatch | null = null;
  for (const field of fields) {
    for (const candidate of field.tokens) {
      const exact = candidate === queryToken;
      const fuzzy = !exact && fuzzyTokenMatch(queryToken, candidate);
      if (!exact && !fuzzy) continue;
      const type: MatchType = exact ? (field.type === "alias" ? "alias" : "token") : "fuzzy";
      const score = exact ? exactWeights[field.type] : fuzzyWeights[field.type];
      if (!best || score > best.score) best = { type, score, candidate };
    }
  }
  return best;
}

function excerpt(text: string, terms: string[]): string {
  const lower = text.toLocaleLowerCase();
  let index = -1;
  for (const term of terms) {
    const found = lower.indexOf(term.toLocaleLowerCase());
    if (found < 0) continue;
    index = found;
    break;
  }
  const start = Math.max(0, index < 0 ? 0 : index - 140);
  const value = text
    .slice(start, start + 420)
    .replace(/\s+/g, " ")
    .trim();
  return `${start > 0 ? "…" : ""}${value}${start + 420 < text.length ? "…" : ""}`;
}

function markdownSummary(text: string): string {
  const heading = text.search(/^## Summary\s*$/m);
  if (heading < 0) return "";
  const contentStart = text.indexOf("\n", heading) + 1;
  const remainder = text.slice(contentStart);
  const nextHeading = remainder.search(/^## /m);
  const summary = (nextHeading < 0 ? remainder : remainder.slice(0, nextHeading))
    .replace(/\s+/g, " ")
    .trim();
  return summary.length > 500 ? `${summary.slice(0, 499).trimEnd()}…` : summary;
}

function atomicWrite(path: string, content: string): void {
  const temporary = join(dirname(path), `.figai-${randomUUID()}.tmp`);
  try {
    writeFileSync(temporary, content, { mode: 0o600 });
    renameSync(temporary, path);
  } finally {
    rmSync(temporary, { force: true });
  }
}

function addSourceToFrontmatter(text: string, link: string, updated: string): string {
  if (!text.startsWith("---\n")) throw new Error("The Brain note has no YAML frontmatter.");
  const end = text.indexOf("\n---\n", 4);
  if (end < 0) throw new Error("The Brain note has invalid YAML frontmatter.");
  const lines = text.slice(4, end).split("\n");
  const updatedIndex = lines.findIndex((line) => line.startsWith("updated:"));
  const sourcesIndex = lines.findIndex((line) => line === "sources:" || line === "sources: []");
  if (updatedIndex < 0 || sourcesIndex < 0) {
    throw new Error("The Brain note is missing required properties.");
  }
  lines[updatedIndex] = `updated: ${updated}`;
  let insertAt = sourcesIndex + 1;
  if (lines[sourcesIndex] === "sources: []") lines[sourcesIndex] = "sources:";
  while (insertAt < lines.length && lines[insertAt]?.startsWith("  - ")) insertAt += 1;
  if (!lines.slice(sourcesIndex + 1, insertAt).some((line) => line?.includes(link))) {
    lines.splice(insertAt, 0, `  - ${yamlQuote(link)}`);
  }
  return `---\n${lines.join("\n")}\n---\n${text.slice(end + 5)}`;
}

function appendManualNotes(text: string, addition: string, date: string): string {
  const heading = "## Manual notes";
  const index = text.indexOf(heading);
  if (index < 0) throw new Error("The Brain note has no Manual notes section.");
  const afterHeading = index + heading.length;
  const nextHeading = text.indexOf("\n## ", afterHeading);
  const insertion = `\n\n### ${date}\n\n${addition.trim()}\n`;
  return nextHeading < 0
    ? `${text.trimEnd()}${insertion}`
    : `${text.slice(0, nextHeading).trimEnd()}${insertion}\n${text.slice(nextHeading + 1)}`;
}

function defaultSection(entryKind: BrainEntryKind): string {
  switch (entryKind) {
    case "task":
      return "Tasks";
    case "list-item":
      return "Items";
    case "fact":
      return "Facts";
    case "prose":
      return "Notes";
  }
}

function formatEntry(text: string, entryKind: BrainEntryKind): string {
  if (entryKind === "prose") return text.trim();
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (entryKind === "task") {
    return lines.map((line) => `- [ ] ${line.replace(/^[-*]\s+(?:\[[ xX]\]\s*)?/, "")}`).join("\n");
  }
  return lines.map((line) => `- ${line.replace(/^[-*]\s+/, "")}`).join("\n");
}

function appendToSection(text: string, section: string, addition: string): string {
  const heading = `## ${section}`;
  const headingPattern = new RegExp(`^${heading.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`, "m");
  const match = headingPattern.exec(text);
  if (match) {
    const afterHeading = match.index + match[0].length;
    const nextHeadingOffset = text.slice(afterHeading).search(/^##\s+/m);
    const insertionPoint = nextHeadingOffset < 0 ? text.length : afterHeading + nextHeadingOffset;
    return `${`${text.slice(0, insertionPoint).trimEnd()}\n\n${addition.trim()}\n\n${text
      .slice(insertionPoint)
      .trimStart()}`.trimEnd()}\n`;
  }

  const manualNotes = text.search(/^## Manual notes\s*$/m);
  const block = `## ${section}\n\n${addition.trim()}\n\n`;
  if (manualNotes < 0) return `${text.trimEnd()}\n\n${block}`;
  return `${text.slice(0, manualNotes).trimEnd()}\n\n${block}${text.slice(manualNotes)}`;
}

function organizedNote(input: {
  title: string;
  noteType: string;
  source: string;
  topics: string[];
  section: string;
  entry: string;
  today: string;
}): string {
  const topicProperties = input.topics.length
    ? `topics:\n${input.topics.map((topic) => `  - ${yamlQuote(topic)}`).join("\n")}`
    : "topics: []";
  const base = `---\ntype: ${input.noteType}\nstatus: seed\ncreated: ${input.today}\nupdated: ${input.today}\nsources:\n  - ${yamlQuote(sourceLink(input.source))}\n${topicProperties}\nconfidence: medium\naliases: []\n---\n\n# ${input.title}\n\n## Summary\n\n## Connections\n\n## Open questions\n\n## Manual notes\n`;
  return appendToSection(base, input.section, input.entry);
}

export class BrainVault implements BrainRepository, BrainMapProvider {
  private readonly root: string;
  private readonly access: BrainAccess;

  constructor(
    root: string,
    access: string | BrainAccess,
    private readonly runner: BrainRunner = new SystemBrainRunner(),
    private readonly mapRenderer: BrainMapRenderer = new SystemBrainMapRenderer(),
  ) {
    this.root = realpathSync(resolve(root));
    this.access = typeof access === "string" ? { kind: "owner", ownerUserId: access } : access;
    for (const required of [".obsidian", "sources", "wiki", "maps", "scripts/vault.py"]) {
      if (!existsSync(join(this.root, required))) {
        throw new Error(`Brain vault is missing required path: ${required}`);
      }
    }
  }

  private authorize(context: RuntimeContext): void {
    if (this.access.workspaceId && context.workspaceId !== this.access.workspaceId) {
      throw new Error("Brain access is not available in this workspace.");
    }
    if (this.access.kind === "owner") {
      if (
        context.requesterId !== this.access.ownerUserId ||
        !context.isOwner ||
        context.surface !== "dm"
      ) {
        throw new Error("Brain access is restricted to the owner in a direct message.");
      }
      return;
    }
    if (this.access.kind === "user") {
      if (context.surface !== "dm" || context.requesterId !== this.access.userId) {
        throw new Error("Private Brain access is restricted to its direct-message user.");
      }
      return;
    }
    if (context.surface !== "channel" || context.channelId !== this.access.channelId) {
      throw new Error("Channel Brain access is restricted to its Slack channel.");
    }
  }

  private relativeMarkdownPath(
    value: string,
    allowedDirectories: readonly string[],
    allowHome = false,
  ): string {
    const requested = `${value.trim().replaceAll("\\", "/").replace(/\.md$/i, "")}.md`;
    if (!requested || isAbsolute(requested) || requested.includes("\0")) {
      throw new Error("That is not a valid Brain note path.");
    }
    const segments = requested.split("/");
    if (segments.some((segment) => !segment || segment === ".." || segment.startsWith("."))) {
      throw new Error("That is not a valid Brain note path.");
    }
    const permitted =
      (allowHome && requested === "Home.md") ||
      allowedDirectories.some((directory) => requested.startsWith(`${directory}/`));
    if (!permitted) throw new Error("That Brain area is not available to FigAi.");
    const unresolved = join(this.root, requested);
    if (!existsSync(unresolved)) throw new Error("Brain note not found.");
    const absolute = realpathSync(unresolved);
    const requestedDirectory = allowedDirectories.find((directory) =>
      requested.startsWith(`${directory}/`),
    );
    const realArea = requestedDirectory ? realpathSync(join(this.root, requestedDirectory)) : null;
    const isInRequestedArea = realArea
      ? absolute.startsWith(`${realArea}${sep}`)
      : allowHome && requested === "Home.md" && absolute === join(this.root, "Home.md");
    if (!isInRequestedArea || !statSync(absolute).isFile()) {
      throw new Error("Brain note not found.");
    }
    return requested;
  }

  private readNote(path: string): string {
    const absolute = join(this.root, path);
    const size = statSync(absolute).size;
    if (size > MAX_NOTE_BYTES) throw new Error("That Brain note is larger than the 1 MB limit.");
    return readFileSync(absolute, "utf8");
  }

  graph(input: { context: RuntimeContext; label: string }): BrainGraph {
    this.authorize(input.context);
    return buildBrainGraph(this.root, input.label);
  }

  exportMap(input: { context: RuntimeContext }): BrainMapExport {
    return this.mapRenderer.render([this.graph({ ...input, label: "Matt-Private" })]);
  }

  list(input: { limit: number; context: RuntimeContext }): unknown {
    this.authorize(input.context);
    const root = join(this.root, "wiki");
    const notes: Array<{ path: string; title: string; summary: string }> = [];
    let inspected = 0;
    for (const entry of new Bun.Glob("**/*.md").scanSync({ cwd: root, onlyFiles: true })) {
      inspected += 1;
      if (inspected > MAX_SEARCH_FILES) {
        throw new Error("The Brain listing limit was exceeded.");
      }
      const unresolved = join(root, entry);
      const absolute = realpathSync(unresolved);
      if (!absolute.startsWith(`${root}${sep}`) || !statSync(absolute).isFile()) continue;
      if (statSync(absolute).size > MAX_NOTE_BYTES) continue;
      const text = readFileSync(absolute, "utf8");
      const path = relative(this.root, absolute).replaceAll(sep, "/");
      notes.push({
        path,
        title: markdownTitle(text, basename(path, ".md")),
        summary: markdownSummary(text),
      });
    }
    notes.sort((a, b) => a.path.localeCompare(b.path));
    return {
      untrusted: true,
      total: notes.length,
      notes: notes.slice(0, input.limit),
      truncated: notes.length > input.limit,
    };
  }

  search(input: { query: string; limit: number; context: RuntimeContext }): unknown {
    this.authorize(input.context);
    const query = cleanText(input.query, 200);
    const normalized = normalizeSearchText(query);
    if (!normalized) throw new Error("Brain search requires at least one letter or number.");
    const queryTokens = searchTokens(query);
    const results: Array<{
      path: string;
      title: string;
      excerpt: string;
      matchedTerms: string[];
      matchType: MatchType;
      score: number;
    }> = [];
    let inspected = 0;
    for (const directory of SEARCH_DIRECTORIES) {
      const root = join(this.root, directory);
      for (const entry of new Bun.Glob("**/*.md").scanSync({ cwd: root, onlyFiles: true })) {
        inspected += 1;
        if (inspected > MAX_SEARCH_FILES) {
          throw new Error("The Brain search limit was exceeded; use a narrower query.");
        }
        const unresolved = join(root, entry);
        const absolute = realpathSync(unresolved);
        if (!absolute.startsWith(`${root}${sep}`) || !statSync(absolute).isFile()) continue;
        if (statSync(absolute).size > MAX_NOTE_BYTES) continue;
        const text = readFileSync(absolute, "utf8");
        const path = relative(this.root, absolute).replaceAll(sep, "/");
        const title = markdownTitle(text, basename(path, ".md"));
        const aliases = markdownAliases(text);
        const body = markdownBody(text);
        const fields: SearchField[] = [
          { type: "title", phrase: normalizeSearchText(title), tokens: searchTokens(title) },
          { type: "path", phrase: normalizeSearchText(path), tokens: searchTokens(path) },
          ...aliases.map(
            (alias): SearchField => ({
              type: "alias",
              phrase: normalizeSearchText(alias),
              tokens: searchTokens(alias),
            }),
          ),
          {
            type: "body",
            phrase: normalizeSearchText(body),
            tokens: searchTokens(body),
          },
        ];
        const phraseMatches = fields.filter((field) => field.phrase.includes(normalized));
        const tokenMatches = queryTokens.map((token) => ({
          token,
          match: matchToken(token, fields),
        }));
        const matched = tokenMatches.filter(
          (item): item is { token: string; match: TokenMatch } => item.match !== null,
        );
        if (!phraseMatches.length && !matched.length) continue;
        const allTermsMatched = matched.length === queryTokens.length;
        const exactPhrase = phraseMatches.some((field) => field.type !== "alias");
        const aliasPhrase = phraseMatches.some((field) => field.type === "alias");
        const hasFuzzy = matched.some((item) => item.match.type === "fuzzy");
        const hasAlias = matched.some((item) => item.match.type === "alias");
        const matchType: MatchType = exactPhrase
          ? "exact"
          : aliasPhrase || hasAlias
            ? "alias"
            : hasFuzzy
              ? "fuzzy"
              : "token";
        const phraseScore = Math.max(
          0,
          ...phraseMatches.map((field) =>
            field.type === "title"
              ? 1000
              : field.type === "alias"
                ? 900
                : field.type === "path"
                  ? 700
                  : 500,
          ),
        );
        const score =
          phraseScore +
          matched.reduce((total, item) => total + item.match.score, 0) +
          (allTermsMatched ? 300 : 0) +
          matched.length * 10;
        const excerptTerms = [
          ...matched
            .filter((item) => item.match.type === "fuzzy")
            .map((item) => item.match.candidate),
          ...matched.map((item) => item.match.candidate),
          ...queryTokens,
        ];
        results.push({
          path,
          title,
          excerpt: excerpt(text, excerptTerms),
          matchedTerms: matched.map((item) => item.token),
          matchType,
          score,
        });
      }
    }
    return {
      untrusted: true,
      query,
      results: results
        .sort((a, b) => b.score - a.score || a.path.localeCompare(b.path))
        .slice(0, input.limit)
        .map(({ score: _, ...result }) => result),
    };
  }

  read(input: { path: string; context: RuntimeContext }): unknown {
    this.authorize(input.context);
    const path = this.relativeMarkdownPath(input.path, READ_DIRECTORIES, true);
    const text = this.readNote(path);
    return {
      untrusted: true,
      path,
      content: text.slice(0, MAX_READ_CHARACTERS),
      truncated: text.length > MAX_READ_CHARACTERS,
    };
  }

  private canonicalNotePath(kind: BrainDestinationKind, title: string): string | null {
    const directory = DESTINATION_CONFIG[kind].directory;
    const root = join(this.root, "wiki", directory);
    if (!existsSync(root)) return null;
    const expected = normalizeSearchText(title);
    const matches: string[] = [];
    for (const entry of new Bun.Glob("**/*.md").scanSync({ cwd: root, onlyFiles: true })) {
      const unresolved = join(root, entry);
      const absolute = realpathSync(unresolved);
      if (!absolute.startsWith(`${root}${sep}`) || !statSync(absolute).isFile()) continue;
      if (statSync(absolute).size > MAX_NOTE_BYTES) continue;
      const text = readFileSync(absolute, "utf8");
      const names = [markdownTitle(text, basename(entry, ".md")), ...markdownAliases(text)];
      if (
        names.some((name) => normalizeSearchText(name) === expected) ||
        normalizeSearchText(basename(entry, ".md")) === expected
      ) {
        matches.push(relative(this.root, absolute).replaceAll(sep, "/"));
      }
    }
    if (matches.length > 1) {
      throw new Error(
        "The Brain has multiple canonical notes with that name; organize them first.",
      );
    }
    return matches[0] ?? null;
  }

  save(input: {
    destinationKind: BrainDestinationKind;
    destinationTitle: string;
    text: string;
    entryKind: BrainEntryKind;
    section?: string;
    category?: string;
    topics: string[];
    context: RuntimeContext;
  }): unknown {
    this.authorize(input.context);
    if (!DESTINATION_KINDS.includes(input.destinationKind)) {
      throw new Error("Invalid Brain destination kind.");
    }
    if (!ENTRY_KINDS.includes(input.entryKind)) throw new Error("Invalid Brain entry kind.");
    const title = cleanText(input.destinationTitle, 120).replace(/[\r\n]+/g, " ");
    const text = cleanText(input.text, MAX_CAPTURE_CHARACTERS).replace(
      /^## Manual notes$/gm,
      "### Manual notes",
    );
    const section = cleanText(input.section || defaultSection(input.entryKind), 80)
      .replace(/^#+\s*/, "")
      .replace(/[\r\n]+/g, " ");
    const category = input.category
      ? slugify(cleanText(input.category, 80).replace(/[\r\n]+/g, " "))
      : null;
    const topics = [...new Set(input.topics.map((topic) => cleanText(topic, 40)))].slice(0, 12);
    const entry = formatEntry(text, input.entryKind);
    const existingPath = this.canonicalNotePath(input.destinationKind, title);
    if (input.entryKind === "task" && input.destinationKind !== "list" && !existingPath) {
      throw new Error(
        "A standalone note cannot be created for one task; save it in a list or an existing subject.",
      );
    }
    const config = DESTINATION_CONFIG[input.destinationKind];
    const slug = slugify(title);
    const wikiPath =
      existingPath ?? `wiki/${config.directory}/${category ? `${category}/` : ""}${slug}.md`;
    const absoluteWiki = join(this.root, wikiPath);
    if (!existingPath && existsSync(absoluteWiki)) {
      throw new Error("A different Brain note already occupies that destination.");
    }

    this.runner.assertClean(this.root);
    const source = this.runner.capture(this.root, {
      title: existingPath ? `Update to ${title}` : title,
      text,
    });
    const today = new Date().toISOString().slice(0, 10);
    const original = existingPath ? this.readNote(existingPath) : null;
    mkdirSync(dirname(absoluteWiki), { recursive: true, mode: 0o700 });
    const updated = original
      ? appendToSection(
          addSourceToFrontmatter(original, sourceLink(source.source), today),
          section,
          entry,
        )
      : organizedNote({
          title,
          noteType: config.noteType,
          source: source.source,
          topics,
          section,
          entry,
          today,
        });
    atomicWrite(absoluteWiki, updated);
    try {
      this.runner.lint(this.root);
    } catch (error) {
      if (original === null) rmSync(absoluteWiki, { force: true });
      else atomicWrite(absoluteWiki, original);
      throw error;
    }
    this.runner.commit(this.root, `vault: ${existingPath ? "update" : "create"} ${slug}`, wikiPath);
    return {
      saved: true,
      operation: existingPath ? "updated" : "created",
      destination: {
        kind: input.destinationKind,
        title,
        section,
      },
      sourceCreated: source.created,
    };
  }

  removeListEntry(input: {
    destinationTitle: string;
    text: string;
    context: RuntimeContext;
  }): unknown {
    this.authorize(input.context);
    const title = cleanText(input.destinationTitle, 120).replace(/[\r\n]+/g, " ");
    const text = cleanText(input.text, 500).replace(/[\r\n]+/g, " ");
    const path = this.canonicalNotePath("list", title);
    if (!path) throw new Error("That Brain list does not exist in the current scope.");

    this.runner.assertClean(this.root);
    const original = this.readNote(path);
    // Validate the target before creating immutable evidence for a rejected change.
    removeExactListEntry(original, text);
    const source = this.runner.capture(this.root, {
      title: `Remove from ${title}`,
      text: `Removed list item after explicit confirmation: ${text}`,
    });
    const today = new Date().toISOString().slice(0, 10);
    const updated = removeExactListEntry(
      addSourceToFrontmatter(original, sourceLink(source.source), today),
      text,
    );
    const absolute = join(this.root, path);
    atomicWrite(absolute, updated);
    try {
      this.runner.lint(this.root);
    } catch (error) {
      atomicWrite(absolute, original);
      throw error;
    }
    this.runner.commit(this.root, `vault: remove entry from ${slugify(title)}`, path);
    return {
      removed: true,
      destination: { kind: "list", title },
      sourceCreated: source.created,
    };
  }

  capture(input: {
    title: string;
    text: string;
    noteType: BrainNoteType;
    topics: string[];
    context: RuntimeContext;
  }): unknown {
    this.authorize(input.context);
    const title = cleanText(input.title, 120).replace(/[\r\n]+/g, " ");
    const text = cleanText(input.text, MAX_CAPTURE_CHARACTERS).replace(
      /^## Manual notes$/gm,
      "### Manual notes",
    );
    if (!NOTE_TYPES.includes(input.noteType)) throw new Error("Invalid Brain note type.");
    const topics = [...new Set(input.topics.map((topic) => cleanText(topic, 40)))].slice(0, 12);
    const slug = slugify(title);
    const wikiPath = `wiki/${TYPE_DIRECTORIES[input.noteType]}/${slug}.md`;
    const absoluteWiki = join(this.root, wikiPath);
    if (existsSync(absoluteWiki)) {
      throw new Error("That Brain note already exists; append to it instead.");
    }
    this.runner.assertClean(this.root);
    const source = this.runner.capture(this.root, { title, text });
    const today = new Date().toISOString().slice(0, 10);
    mkdirSync(dirname(absoluteWiki), { recursive: true, mode: 0o700 });
    const topicProperties = topics.length
      ? `topics:\n${topics.map((topic) => `  - ${yamlQuote(topic)}`).join("\n")}`
      : "topics: []";
    const note = `---\ntype: ${input.noteType}\nstatus: seed\ncreated: ${today}\nupdated: ${today}\nsources:\n  - ${yamlQuote(sourceLink(source.source))}\n${topicProperties}\nconfidence: medium\naliases: []\n---\n\n# ${title}\n\n## Summary\n\n${text}\n\n## Connections\n\n## Open questions\n\n## Manual notes\n`;
    atomicWrite(absoluteWiki, note);
    try {
      this.runner.lint(this.root);
    } catch (error) {
      rmSync(absoluteWiki, { force: true });
      throw error;
    }
    this.runner.commit(this.root, `vault: capture ${slug}`, wikiPath);
    return {
      captured: true,
      source: source.source,
      note: wikiPath,
      sourceCreated: source.created,
    };
  }

  append(input: { path: string; text: string; context: RuntimeContext }): unknown {
    this.authorize(input.context);
    const path = this.relativeMarkdownPath(input.path, ["wiki"]);
    const text = cleanText(input.text, MAX_CAPTURE_CHARACTERS).replace(
      /^## Manual notes$/gm,
      "### Manual notes",
    );
    this.runner.assertClean(this.root);
    const title = markdownTitle(this.readNote(path), basename(path, ".md"));
    const source = this.runner.capture(this.root, { title: `Update to ${title}`, text });
    const absolute = join(this.root, path);
    const original = this.readNote(path);
    const today = new Date().toISOString().slice(0, 10);
    const withSource = addSourceToFrontmatter(original, sourceLink(source.source), today);
    atomicWrite(absolute, appendManualNotes(withSource, text, today));
    try {
      this.runner.lint(this.root);
    } catch (error) {
      atomicWrite(absolute, original);
      throw error;
    }
    this.runner.commit(this.root, `vault: update ${slugify(title)}`, path);
    return {
      appended: true,
      source: source.source,
      note: path,
      sourceCreated: source.created,
    };
  }
}
