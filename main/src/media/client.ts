export type MediaServiceName = "sonarr" | "radarr" | "sabnzbd";
export type MediaView =
  | "status"
  | "library"
  | "missing"
  | "queue"
  | "history"
  | "quality_profiles"
  | "root_folders"
  | "naming"
  | "download_clients"
  | "categories"
  | "configuration";
export type MediaConfigSection = "general" | "folders" | "servers" | "categories";

export interface MediaConnection {
  baseUrl: string;
  apiKey: string;
}

export interface MediaConnections {
  sonarr: MediaConnection | null;
  radarr: MediaConnection | null;
  sabnzbd: MediaConnection | null;
}

interface InspectInput {
  service: MediaServiceName;
  view: MediaView;
  query?: string;
  limit: number;
  configSection?: MediaConfigSection;
}

export interface AddMediaInput {
  kind: "movie" | "series";
  title: string;
  year?: number;
  rootFolder?: string;
  qualityProfile?: string;
  searchNow: boolean;
}

export interface SonarrEpisodeSelector {
  seasonNumber: number;
  episodeNumber: number;
}

export type SonarrEpisodeActionInput =
  | {
      action: "search_episodes";
      seriesTitle: string;
      year?: number;
      episodes: SonarrEpisodeSelector[];
    }
  | {
      action: "search_season";
      seriesTitle: string;
      year?: number;
      seasonNumber: number;
    }
  | {
      action: "delete_episode_files";
      seriesTitle: string;
      year?: number;
      episodes: SonarrEpisodeSelector[];
      allowSharedFiles: boolean;
    };

export type MediaFetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_RESULT_CHARACTERS = 45_000;
const SECRET_FIELD =
  /(^|[_-])(api[_-]?key|apikey|password|passwd|secret|token|cookie|credential|certificate|private[_-]?key|username|email)($|[_-])/i;

function endpoint(baseUrl: string, pathname: string): URL {
  const url = new URL(baseUrl);
  const prefix = url.pathname.replace(/\/+$/, "");
  url.pathname = `${prefix}${pathname.startsWith("/") ? pathname : `/${pathname}`}`;
  url.search = "";
  url.hash = "";
  return url;
}

function sanitize(value: unknown, arrayLimit: number, depth = 0): unknown {
  if (depth > 10) return "[nested data omitted]";
  if (Array.isArray(value)) {
    const items = value.slice(0, arrayLimit).map((item) => sanitize(item, arrayLimit, depth + 1));
    if (value.length > arrayLimit) items.push(`[${value.length - arrayLimit} more items omitted]`);
    return items;
  }
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  const descriptor = [record.name, record.label, record.key, record.keyword]
    .filter((item): item is string => typeof item === "string")
    .join(" ");
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => {
      if (
        SECRET_FIELD.test(key) ||
        (key.toLowerCase() === "value" && SECRET_FIELD.test(descriptor))
      ) {
        return [key, "[REDACTED]"];
      }
      return [key, sanitize(item, arrayLimit, depth + 1)];
    }),
  );
}

function boundedData(data: unknown, limit: number): Record<string, unknown> {
  const sanitized = sanitize(data, limit);
  const serialized = JSON.stringify(sanitized);
  if (serialized.length <= MAX_RESULT_CHARACTERS) return { truncated: false, data: sanitized };
  return {
    truncated: true,
    dataPreview: serialized.slice(0, MAX_RESULT_CHARACTERS),
    note: "The sanitized response was too large and has been truncated.",
  };
}

function filterLibrary(data: unknown, query: string | undefined, limit: number): unknown {
  if (!Array.isArray(data)) return data;
  const normalized = query?.trim().toLowerCase();
  const filtered = normalized
    ? data.filter((item) => {
        if (!item || typeof item !== "object") return false;
        const record = item as Record<string, unknown>;
        return [record.title, record.sortTitle, record.path]
          .filter((value): value is string => typeof value === "string")
          .some((value) => value.toLowerCase().includes(normalized));
      })
    : data;
  return filtered.slice(0, limit);
}

type JsonRecord = Record<string, unknown>;

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is JsonRecord =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function positiveInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : undefined;
}

function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function normalized(value: string): string {
  return value
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}

function pathDepth(path: string): number {
  return path.split(/[\\/]/).filter(Boolean).length;
}

function chooseRootFolder(
  service: "sonarr" | "radarr",
  available: JsonRecord[],
  requested?: string,
): string {
  const paths = available
    .filter((item) => item.accessible !== false)
    .map((item) => text(item.path))
    .filter((item): item is string => !!item);
  if (requested) {
    const wanted = normalized(requested);
    const matches = paths.filter(
      (path) => normalized(path) === wanted || normalized(folderName(path)) === wanted,
    );
    if (matches.length === 1) return matches[0] as string;
    throw new Error(`${service} does not have one uniquely matching root folder.`);
  }
  if (paths.length === 1) return paths[0] as string;
  const conventionalName = service === "sonarr" ? "tv" : "movies";
  const conventional = paths.filter((path) => normalized(folderName(path)) === conventionalName);
  const shallowestDepth = Math.min(...conventional.map(pathDepth));
  const shallowest = conventional.filter((path) => pathDepth(path) === shallowestDepth);
  if (shallowest.length === 1) return shallowest[0] as string;
  throw new Error(`${service} has multiple root folders and no default could be inferred.`);
}

function chooseQualityProfile(
  service: "sonarr" | "radarr",
  available: JsonRecord[],
  requested?: string,
): number {
  const profiles = available
    .map((item) => ({ id: positiveInteger(item.id), name: text(item.name) }))
    .filter((item): item is { id: number; name: string } => !!item.id && !!item.name);
  if (requested) {
    const wanted = normalized(requested);
    const matches = profiles.filter((profile) => normalized(profile.name) === wanted);
    const match = matches[0];
    if (matches.length === 1 && match) return match.id;
    throw new Error(`${service} does not have one uniquely matching quality profile.`);
  }
  const onlyProfile = profiles[0];
  if (profiles.length === 1 && onlyProfile) return onlyProfile.id;
  const conventional = profiles.filter((profile) => normalized(profile.name) === "any");
  const defaultProfile = conventional[0];
  if (conventional.length === 1 && defaultProfile) return defaultProfile.id;
  throw new Error(`${service} has multiple quality profiles and no default could be inferred.`);
}

function candidateSummary(candidate: JsonRecord): Record<string, unknown> {
  return {
    title: text(candidate.title) ?? "Unknown title",
    ...(positiveInteger(candidate.year) ? { year: candidate.year } : {}),
  };
}

function episodeSummary(episode: JsonRecord): Record<string, unknown> {
  return {
    seasonNumber: nonNegativeInteger(episode.seasonNumber) ?? 0,
    episodeNumber: nonNegativeInteger(episode.episodeNumber) ?? 0,
    title: text(episode.title) ?? "Unknown episode",
    hasFile: episode.hasFile === true,
  };
}

function episodeKey(selector: SonarrEpisodeSelector): string {
  return `${selector.seasonNumber}:${selector.episodeNumber}`;
}

export class MediaServiceClient {
  constructor(
    private readonly connections: MediaConnections,
    private readonly fetcher: MediaFetcher = fetch,
  ) {}

  async inspect(input: InspectInput): Promise<Record<string, unknown>> {
    const connection = this.connections[input.service];
    if (!connection) throw new Error(`${input.service} is not configured.`);
    const data =
      input.service === "sabnzbd"
        ? await this.inspectSabnzbd(connection, input)
        : await this.inspectArr(input.service, connection, input);
    return {
      untrusted: true,
      service: input.service,
      view: input.view,
      ...boundedData(data, input.limit),
    };
  }

  async add(input: AddMediaInput): Promise<Record<string, unknown>> {
    const service = input.kind === "movie" ? "radarr" : "sonarr";
    const connection = this.connections[service];
    if (!connection) throw new Error(`${service} is not configured.`);
    const resource = input.kind === "movie" ? "movie" : "series";
    const externalId = input.kind === "movie" ? "tmdbId" : "tvdbId";
    const lookup = records(
      await this.arrGet(service, connection, `/api/v3/${resource}/lookup`, {
        term: input.title,
      }),
    );
    const exactTitle = lookup.filter(
      (candidate) => normalized(text(candidate.title) ?? "") === normalized(input.title),
    );
    const matching = input.year
      ? exactTitle.filter((candidate) => positiveInteger(candidate.year) === input.year)
      : exactTitle;
    const unique = matching.filter((candidate) => positiveInteger(candidate[externalId]));
    if (unique.length !== 1) {
      const candidates = (exactTitle.length ? exactTitle : lookup).slice(0, 5);
      return {
        untrusted: true,
        service,
        kind: input.kind,
        added: false,
        requiresDisambiguation: true,
        candidates: candidates.map(candidateSummary),
      };
    }

    const candidate = unique[0];
    const candidateId = candidate ? positiveInteger(candidate[externalId]) : undefined;
    if (!candidate || !candidateId) throw new Error(`${service} could not resolve that title.`);
    const [existingMatches, rootFolders, qualityProfiles] = await Promise.all([
      this.arrGet(service, connection, `/api/v3/${resource}`, {
        [externalId]: String(candidateId),
      }),
      this.arrGet(service, connection, "/api/v3/rootfolder"),
      this.arrGet(service, connection, "/api/v3/qualityprofile"),
    ]);
    const existing = records(existingMatches).find(
      (item) => positiveInteger(item[externalId]) === candidateId,
    );
    if (existing) {
      return {
        untrusted: true,
        service,
        kind: input.kind,
        added: false,
        alreadyExists: true,
        ...candidateSummary(existing),
      };
    }

    const rootFolderPath = chooseRootFolder(service, records(rootFolders), input.rootFolder);
    const qualityProfileId = chooseQualityProfile(
      service,
      records(qualityProfiles),
      input.qualityProfile,
    );
    const title = text(candidate.title) ?? input.title;
    const common = {
      title,
      year: positiveInteger(candidate.year),
      titleSlug: text(candidate.titleSlug),
      images: Array.isArray(candidate.images) ? candidate.images : [],
      qualityProfileId,
      rootFolderPath,
      monitored: true,
      tags: [],
    };
    const body: JsonRecord =
      input.kind === "movie"
        ? {
            ...common,
            tmdbId: candidateId,
            minimumAvailability: text(candidate.minimumAvailability) ?? "announced",
            addOptions: { searchForMovie: input.searchNow },
          }
        : {
            ...common,
            tvdbId: candidateId,
            seriesType: text(candidate.seriesType) ?? "standard",
            seasonFolder: true,
            monitorNewItems: "all",
            seasons: records(candidate.seasons)
              .map((season) => positiveInteger(season.seasonNumber) ?? 0)
              .map((seasonNumber) => ({ seasonNumber, monitored: seasonNumber !== 0 })),
            addOptions: {
              monitor: "all",
              searchForMissingEpisodes: input.searchNow,
              searchForCutoffUnmetEpisodes: false,
            },
          };
    const created = await this.arrPost(service, connection, `/api/v3/${resource}`, body);
    const createdRecord =
      created && typeof created === "object" && !Array.isArray(created)
        ? (created as JsonRecord)
        : null;
    if (!createdRecord || positiveInteger(createdRecord[externalId]) !== candidateId) {
      throw new Error(`${service} did not confirm the requested addition.`);
    }
    return {
      untrusted: true,
      service,
      kind: input.kind,
      added: true,
      ...candidateSummary(createdRecord),
      monitored: true,
      searched: input.searchNow,
    };
  }

  async manageSonarrEpisodes(input: SonarrEpisodeActionInput): Promise<Record<string, unknown>> {
    const connection = this.connections.sonarr;
    if (!connection) throw new Error("sonarr is not configured.");
    const lookup = records(
      await this.arrGet("sonarr", connection, "/api/v3/series/lookup", {
        term: input.seriesTitle,
      }),
    );
    const exactTitle = lookup.filter(
      (candidate) => normalized(text(candidate.title) ?? "") === normalized(input.seriesTitle),
    );
    const matching = input.year
      ? exactTitle.filter((candidate) => positiveInteger(candidate.year) === input.year)
      : exactTitle;
    const selectedCandidate = matching[0];
    const tvdbId = selectedCandidate ? positiveInteger(selectedCandidate.tvdbId) : undefined;
    if (matching.length !== 1 || !selectedCandidate || !tvdbId) {
      const nearby = lookup
        .filter((candidate) => {
          const title = normalized(text(candidate.title) ?? "");
          const wanted = normalized(input.seriesTitle);
          return title.includes(wanted) || wanted.includes(title);
        })
        .slice(0, 5);
      return {
        untrusted: true,
        service: "sonarr",
        action: input.action,
        performed: false,
        requiresDisambiguation: true,
        candidates: (exactTitle.length ? exactTitle : nearby).map(candidateSummary),
      };
    }
    const libraryMatches = records(
      await this.arrGet("sonarr", connection, "/api/v3/series", {
        tvdbId: String(tvdbId),
      }),
    );
    const selectedSeries = libraryMatches.find(
      (candidate) => positiveInteger(candidate.tvdbId) === tvdbId,
    );
    const seriesId = selectedSeries ? positiveInteger(selectedSeries.id) : undefined;
    if (!selectedSeries || !seriesId) {
      throw new Error("That series is not currently in the configured Sonarr library.");
    }

    const allEpisodes = records(
      await this.arrGet("sonarr", connection, "/api/v3/episode", {
        seriesId: String(seriesId),
      }),
    );
    if (input.action === "search_season") {
      const seasonEpisodes = allEpisodes.filter(
        (episode) => nonNegativeInteger(episode.seasonNumber) === input.seasonNumber,
      );
      if (!seasonEpisodes.length) {
        throw new Error(`Sonarr has no season ${input.seasonNumber} episodes for that series.`);
      }
      const command = await this.arrPost("sonarr", connection, "/api/v3/command", {
        name: "SeasonSearch",
        seriesId,
        seasonNumber: input.seasonNumber,
      });
      this.requireQueuedCommand(command, "SeasonSearch");
      return {
        untrusted: true,
        service: "sonarr",
        action: input.action,
        performed: true,
        queued: true,
        series: candidateSummary(selectedSeries),
        seasonNumber: input.seasonNumber,
        episodeCount: seasonEpisodes.length,
      };
    }

    const selectors = [...new Map(input.episodes.map((item) => [episodeKey(item), item])).values()];
    const selectedEpisodes = selectors.map((selector) => {
      const matches = allEpisodes.filter(
        (episode) =>
          nonNegativeInteger(episode.seasonNumber) === selector.seasonNumber &&
          nonNegativeInteger(episode.episodeNumber) === selector.episodeNumber,
      );
      if (matches.length !== 1 || !matches[0]) {
        throw new Error(
          `Sonarr could not uniquely resolve S${selector.seasonNumber}E${selector.episodeNumber}.`,
        );
      }
      return matches[0];
    });

    if (input.action === "search_episodes") {
      const episodeIds = selectedEpisodes.map((episode) => positiveInteger(episode.id));
      if (episodeIds.some((id) => id === undefined)) {
        throw new Error("Sonarr returned an episode without a usable identifier.");
      }
      const command = await this.arrPost("sonarr", connection, "/api/v3/command", {
        name: "EpisodeSearch",
        episodeIds: episodeIds as number[],
      });
      this.requireQueuedCommand(command, "EpisodeSearch");
      return {
        untrusted: true,
        service: "sonarr",
        action: input.action,
        performed: true,
        queued: true,
        series: candidateSummary(selectedSeries),
        episodes: selectedEpisodes.map(episodeSummary),
      };
    }

    const missingFiles = selectedEpisodes.filter(
      (episode) => episode.hasFile !== true || !positiveInteger(episode.episodeFileId),
    );
    if (missingFiles.length) {
      return {
        untrusted: true,
        service: "sonarr",
        action: input.action,
        performed: false,
        noFileForEpisodes: missingFiles.map(episodeSummary),
      };
    }
    const selectedKeys = new Set(
      selectedEpisodes.map(
        (episode) =>
          `${nonNegativeInteger(episode.seasonNumber)}:${nonNegativeInteger(episode.episodeNumber)}`,
      ),
    );
    const fileIds = [
      ...new Set(
        selectedEpisodes.map((episode) => positiveInteger(episode.episodeFileId) as number),
      ),
    ];
    const additionallyAffected = allEpisodes.filter(
      (episode) =>
        fileIds.includes(positiveInteger(episode.episodeFileId) ?? -1) &&
        !selectedKeys.has(
          `${nonNegativeInteger(episode.seasonNumber)}:${nonNegativeInteger(episode.episodeNumber)}`,
        ),
    );
    if (additionallyAffected.length && !input.allowSharedFiles) {
      return {
        untrusted: true,
        service: "sonarr",
        action: input.action,
        performed: false,
        requiresSharedFileConfirmation: true,
        selectedEpisodes: selectedEpisodes.map(episodeSummary),
        additionallyAffectedEpisodes: additionallyAffected.map(episodeSummary),
      };
    }
    await this.arrDelete("sonarr", connection, "/api/v3/episodefile/bulk", {
      episodeFileIds: fileIds,
    });
    return {
      untrusted: true,
      service: "sonarr",
      action: input.action,
      performed: true,
      deleted: true,
      series: candidateSummary(selectedSeries),
      selectedEpisodes: selectedEpisodes.map(episodeSummary),
      additionallyAffectedEpisodes: additionallyAffected.map(episodeSummary),
      deletedFileCount: fileIds.length,
    };
  }

  private requireQueuedCommand(command: unknown, expectedName: string): void {
    if (!command || typeof command !== "object" || Array.isArray(command)) {
      throw new Error("Sonarr did not confirm that the episode search was queued.");
    }
    const record = command as JsonRecord;
    if (!positiveInteger(record.id) || (text(record.name) && text(record.name) !== expectedName)) {
      throw new Error("Sonarr did not confirm that the episode search was queued.");
    }
  }

  private async inspectArr(
    service: "sonarr" | "radarr",
    connection: MediaConnection,
    input: InspectInput,
  ): Promise<unknown> {
    const plural = service === "sonarr" ? "series" : "movie";
    switch (input.view) {
      case "status":
        return this.arrGet(service, connection, "/api/v3/system/status");
      case "library":
        return filterLibrary(
          await this.arrGet(service, connection, `/api/v3/${plural}`),
          input.query,
          input.limit,
        );
      case "missing":
        return this.arrGet(service, connection, "/api/v3/wanted/missing", {
          page: "1",
          pageSize: String(input.limit),
          sortDirection: "descending",
        });
      case "queue":
        return this.arrGet(service, connection, "/api/v3/queue", {
          page: "1",
          pageSize: String(input.limit),
          includeUnknownSeriesItems: "true",
          includeUnknownMovieItems: "true",
        });
      case "history":
        return this.arrGet(service, connection, "/api/v3/history", {
          page: "1",
          pageSize: String(input.limit),
          sortDirection: "descending",
          sortKey: "date",
        });
      case "quality_profiles":
        return this.arrGet(service, connection, "/api/v3/qualityprofile");
      case "root_folders":
        return this.arrGet(service, connection, "/api/v3/rootfolder");
      case "naming":
        return this.arrGet(service, connection, "/api/v3/config/naming");
      case "download_clients":
        return this.arrGet(service, connection, "/api/v3/downloadclient");
      case "configuration":
        return this.arrConfiguration(service, connection, input.configSection ?? "general");
      case "categories":
        throw new Error(`${service} does not expose a categories view through this tool.`);
    }
  }

  private arrConfiguration(
    service: "sonarr" | "radarr",
    connection: MediaConnection,
    section: MediaConfigSection,
  ): Promise<unknown> {
    if (section === "general") return this.arrGet(service, connection, "/api/v3/config/host");
    if (section === "folders") return this.arrGet(service, connection, "/api/v3/rootfolder");
    if (section === "servers") return this.arrGet(service, connection, "/api/v3/downloadclient");
    throw new Error(`${service} does not expose category configuration through this tool.`);
  }

  private async arrGet(
    service: "sonarr" | "radarr",
    connection: MediaConnection,
    path: string,
    query: Record<string, string> = {},
  ): Promise<unknown> {
    const url = endpoint(connection.baseUrl, path);
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
    return this.getJson(service, url, { "X-Api-Key": connection.apiKey });
  }

  private async arrPost(
    service: "sonarr" | "radarr",
    connection: MediaConnection,
    path: string,
    body: JsonRecord,
  ): Promise<unknown> {
    const url = endpoint(connection.baseUrl, path);
    return this.requestJson(service, url, {
      method: "POST",
      headers: { "X-Api-Key": connection.apiKey },
      body,
    });
  }

  private async arrDelete(
    service: "sonarr" | "radarr",
    connection: MediaConnection,
    path: string,
    body: JsonRecord,
  ): Promise<unknown> {
    const url = endpoint(connection.baseUrl, path);
    return this.requestJson(service, url, {
      method: "DELETE",
      headers: { "X-Api-Key": connection.apiKey },
      body,
    });
  }

  private inspectSabnzbd(connection: MediaConnection, input: InspectInput): Promise<unknown> {
    const query: Record<string, string> = { output: "json", apikey: connection.apiKey };
    switch (input.view) {
      case "status":
        query.mode = "fullstatus";
        break;
      case "queue":
        query.mode = "queue";
        query.start = "0";
        query.limit = String(input.limit);
        if (input.query) query.search = input.query;
        break;
      case "history":
        query.mode = "history";
        query.start = "0";
        query.limit = String(input.limit);
        if (input.query) query.search = input.query;
        break;
      case "categories":
        query.mode = "get_cats";
        break;
      case "configuration":
        query.mode = "get_config";
        query.section = {
          general: "misc",
          folders: "folders",
          servers: "servers",
          categories: "categories",
        }[input.configSection ?? "general"];
        break;
      case "library":
      case "missing":
      case "quality_profiles":
      case "root_folders":
      case "naming":
      case "download_clients":
        throw new Error(`sabnzbd does not support the ${input.view} view.`);
    }
    const url = endpoint(connection.baseUrl, "/api");
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
    return this.getJson("sabnzbd", url);
  }

  private async getJson(
    service: MediaServiceName,
    url: URL,
    headers: Record<string, string> = {},
  ): Promise<unknown> {
    return this.requestJson(service, url, { method: "GET", headers });
  }

  private async requestJson(
    service: MediaServiceName,
    url: URL,
    input: {
      method: "GET" | "POST" | "DELETE";
      headers?: Record<string, string>;
      body?: JsonRecord;
    },
  ): Promise<unknown> {
    let response: Response;
    try {
      response = await this.fetcher(url, {
        method: input.method,
        headers: {
          Accept: "application/json",
          ...(input.body ? { "Content-Type": "application/json" } : {}),
          ...input.headers,
        },
        ...(input.body ? { body: JSON.stringify(input.body) } : {}),
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
      });
    } catch {
      throw new Error(`${service} could not be reached at its configured local address.`);
    }
    if (!response.ok) throw new Error(`${service} returned HTTP ${response.status}.`);
    const declared = Number(response.headers.get("content-length"));
    if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
      throw new Error(`${service} returned more than FigAi's 2 MB response limit.`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.byteLength > MAX_RESPONSE_BYTES) {
      throw new Error(`${service} returned more than FigAi's 2 MB response limit.`);
    }
    if (bytes.byteLength === 0) return {};
    let parsed: unknown;
    try {
      parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    } catch {
      throw new Error(`${service} returned an invalid JSON response.`);
    }
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const error = (parsed as Record<string, unknown>).error;
      if (typeof error === "string" && error.trim())
        throw new Error(`${service} reported an API error.`);
    }
    return parsed;
  }
}
