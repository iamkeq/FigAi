import { describe, expect, test } from "bun:test";
import { type MediaFetcher, MediaServiceClient } from "../src/media/client.ts";

const connections = {
  sonarr: { baseUrl: "http://127.0.0.1:8989/sonarr", apiKey: "sonarr-master-key" },
  radarr: { baseUrl: "http://192.168.1.20:7878", apiKey: "radarr-master-key" },
  sabnzbd: { baseUrl: "http://localhost:8080/sabnzbd", apiKey: "sab-master-key" },
};

function json(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("scoped local media client", () => {
  test("uses the exact Sonarr origin, GET, and header authentication", async () => {
    const calls: Array<{ url: URL; init?: RequestInit }> = [];
    const fetcher: MediaFetcher = async (input, init) => {
      calls.push({ url: new URL(String(input)), ...(init === undefined ? {} : { init }) });
      return json({
        version: "5.0.0",
        apiKey: "must-not-reach-model",
        fields: [{ name: "password", value: "also-secret" }],
      });
    };
    const result = await new MediaServiceClient(connections, fetcher).inspect({
      service: "sonarr",
      view: "status",
      limit: 20,
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]?.url.toString()).toBe("http://127.0.0.1:8989/sonarr/api/v3/system/status");
    expect(calls[0]?.init?.method).toBe("GET");
    expect(calls[0]?.init?.redirect).toBe("error");
    expect(new Headers(calls[0]?.init?.headers).get("X-Api-Key")).toBe("sonarr-master-key");
    expect(JSON.stringify(result)).not.toContain("must-not-reach-model");
    expect(JSON.stringify(result)).not.toContain("also-secret");
    expect(result).toMatchObject({ untrusted: true, service: "sonarr", view: "status" });
  });

  test("filters and limits library results locally", async () => {
    const fetcher: MediaFetcher = async () =>
      json([
        { title: "The Expanse", path: "/tv/The Expanse", apiKey: "hidden" },
        { title: "Severance", path: "/tv/Severance" },
        { title: "Expedition Unknown", path: "/tv/Expedition Unknown" },
      ]);
    const result = await new MediaServiceClient(connections, fetcher).inspect({
      service: "sonarr",
      view: "library",
      query: "exp",
      limit: 1,
    });
    expect(result.data).toEqual([
      { title: "The Expanse", path: "/tv/The Expanse", apiKey: "[REDACTED]" },
    ]);
  });

  test("constructs only allowlisted SABnzbd read requests and redacts configuration", async () => {
    const calls: URL[] = [];
    const fetcher: MediaFetcher = async (input, init) => {
      expect(init?.method).toBe("GET");
      const url = new URL(String(input));
      calls.push(url);
      return json({
        config: {
          servers: [
            {
              name: "news-provider",
              host: "news.example.test",
              username: "matt",
              password: "secret-password",
              fields: [{ name: "api_key", value: "nested-secret" }],
            },
          ],
        },
      });
    };
    const result = await new MediaServiceClient(connections, fetcher).inspect({
      service: "sabnzbd",
      view: "configuration",
      configSection: "servers",
      limit: 20,
    });
    expect(calls[0]?.pathname).toBe("/sabnzbd/api");
    expect(calls[0]?.searchParams.get("mode")).toBe("get_config");
    expect(calls[0]?.searchParams.get("section")).toBe("servers");
    expect(calls[0]?.searchParams.get("apikey")).toBe("sab-master-key");
    const serialized = JSON.stringify(result);
    expect(serialized).not.toContain("matt");
    expect(serialized).not.toContain("secret-password");
    expect(serialized).not.toContain("nested-secret");
    expect(serialized).not.toContain("sab-master-key");
    expect(serialized).toContain("news.example.test");
  });

  test("rejects unsupported views and hides transport details", async () => {
    const unavailable = new MediaServiceClient(
      { sonarr: null, radarr: null, sabnzbd: null },
      async () => json({}),
    );
    await expect(
      unavailable.inspect({ service: "radarr", view: "status", limit: 20 }),
    ).rejects.toThrow("not configured");

    const client = new MediaServiceClient(connections, async () => {
      throw new Error("connection to http://localhost:8080/?apikey=leaked failed");
    });
    await expect(
      client.inspect({ service: "sabnzbd", view: "library", limit: 20 }),
    ).rejects.toThrow("does not support");
    await expect(client.inspect({ service: "sabnzbd", view: "status", limit: 20 })).rejects.toThrow(
      "configured local address",
    );
  });

  test("adds an exact Radarr movie using uniquely conventional configured defaults", async () => {
    const calls: Array<{ url: URL; init?: RequestInit }> = [];
    const fetcher: MediaFetcher = async (input, init) => {
      const url = new URL(String(input));
      calls.push({ url, ...(init ? { init } : {}) });
      if (url.pathname.endsWith("/movie/lookup")) {
        return json([
          {
            title: "Arrival",
            year: 2016,
            tmdbId: 329865,
            titleSlug: "arrival-329865",
            images: [{ coverType: "poster", remoteUrl: "https://image.example/poster.jpg" }],
            minimumAvailability: "released",
          },
        ]);
      }
      if (url.pathname.endsWith("/rootfolder")) {
        return json([
          { id: 1, path: "G:\\Movies" },
          { id: 2, path: "G:\\Kids\\Movies" },
        ]);
      }
      if (url.pathname.endsWith("/qualityprofile")) {
        return json([
          { id: 1, name: "Any" },
          { id: 7, name: "Low Quality" },
        ]);
      }
      if (url.pathname.endsWith("/movie") && init?.method === "GET") {
        expect(url.searchParams.get("tmdbId")).toBe("329865");
        return json([]);
      }
      if (url.pathname.endsWith("/movie") && init?.method === "POST") {
        return json({ title: "Arrival", year: 2016, tmdbId: 329865 });
      }
      throw new Error(`Unexpected request: ${url}`);
    };

    const result = await new MediaServiceClient(connections, fetcher).add({
      kind: "movie",
      title: "Arrival",
      year: 2016,
      searchNow: true,
    });
    const post = calls.find((call) => call.init?.method === "POST");
    expect(post?.url.toString()).toBe("http://192.168.1.20:7878/api/v3/movie");
    expect(new Headers(post?.init?.headers).get("X-Api-Key")).toBe("radarr-master-key");
    const body = JSON.parse(String(post?.init?.body));
    expect(body).toMatchObject({
      title: "Arrival",
      year: 2016,
      tmdbId: 329865,
      rootFolderPath: "G:\\Movies",
      qualityProfileId: 1,
      monitored: true,
      addOptions: { searchForMovie: true },
    });
    expect(JSON.stringify(body)).not.toContain("radarr-master-key");
    expect(result).toEqual({
      untrusted: true,
      service: "radarr",
      kind: "movie",
      added: true,
      title: "Arrival",
      year: 2016,
      monitored: true,
      searched: true,
    });
  });

  test("adds a Sonarr series with explicit named choices and no immediate search", async () => {
    let posted: Record<string, unknown> | undefined;
    const fetcher: MediaFetcher = async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/series/lookup")) {
        return json([
          {
            title: "Frieren: Beyond Journey's End",
            year: 2023,
            tvdbId: 424536,
            titleSlug: "frieren-beyond-journeys-end",
            seriesType: "anime",
            seasons: [{ seasonNumber: 0 }, { seasonNumber: 1 }],
          },
        ]);
      }
      if (url.pathname.endsWith("/rootfolder")) {
        return json([
          { id: 1, path: "G:\\TV" },
          { id: 2, path: "G:\\Anime" },
        ]);
      }
      if (url.pathname.endsWith("/qualityprofile")) {
        return json([
          { id: 1, name: "Any" },
          { id: 4, name: "HD-1080p" },
        ]);
      }
      if (url.pathname.endsWith("/series") && init?.method === "GET") {
        expect(url.searchParams.get("tvdbId")).toBe("424536");
        return json([]);
      }
      if (url.pathname.endsWith("/series") && init?.method === "POST") {
        posted = JSON.parse(String(init.body));
        return json({ title: "Frieren: Beyond Journey's End", year: 2023, tvdbId: 424536 });
      }
      throw new Error(`Unexpected request: ${url}`);
    };

    const result = await new MediaServiceClient(connections, fetcher).add({
      kind: "series",
      title: "Frieren: Beyond Journey's End",
      year: 2023,
      rootFolder: "Anime",
      qualityProfile: "HD-1080p",
      searchNow: false,
    });
    expect(posted).toMatchObject({
      tvdbId: 424536,
      rootFolderPath: "G:\\Anime",
      qualityProfileId: 4,
      seriesType: "anime",
      seasonFolder: true,
      addOptions: { monitor: "all", searchForMissingEpisodes: false },
      seasons: [
        { seasonNumber: 0, monitored: false },
        { seasonNumber: 1, monitored: true },
      ],
    });
    expect(result).toMatchObject({
      service: "sonarr",
      kind: "series",
      added: true,
      searched: false,
    });
  });

  test("makes no change for ambiguous or already-added titles", async () => {
    let requests = 0;
    const ambiguous = new MediaServiceClient(connections, async () => {
      requests += 1;
      return json([
        { title: "Crash", year: 1996, tmdbId: 884 },
        { title: "Crash", year: 2004, tmdbId: 1640 },
      ]);
    });
    const unresolved = await ambiguous.add({
      kind: "movie",
      title: "Crash",
      searchNow: true,
    });
    expect(unresolved).toMatchObject({ added: false, requiresDisambiguation: true });
    expect(unresolved.candidates).toEqual([
      { title: "Crash", year: 1996 },
      { title: "Crash", year: 2004 },
    ]);
    expect(requests).toBe(1);

    let posted = false;
    const duplicate = new MediaServiceClient(connections, async (input, init) => {
      const url = new URL(String(input));
      if (init?.method === "POST") posted = true;
      if (url.pathname.endsWith("/movie/lookup"))
        return json([{ title: "Arrival", year: 2016, tmdbId: 329865 }]);
      if (url.pathname.endsWith("/movie")) {
        expect(url.searchParams.get("tmdbId")).toBe("329865");
        return json([{ title: "Arrival", year: 2016, tmdbId: 329865 }]);
      }
      return json([]);
    });
    const existing = await duplicate.add({
      kind: "movie",
      title: "Arrival",
      year: 2016,
      searchNow: true,
    });
    expect(existing).toMatchObject({ added: false, alreadyExists: true, title: "Arrival" });
    expect(posted).toBe(false);
  });

  test("never downloads an oversized full library before adding", async () => {
    let requestedFullLibrary = false;
    let posted = false;
    const client = new MediaServiceClient(connections, async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/series/lookup")) {
        return json([
          {
            title: "Widow's Bay",
            year: 2026,
            tvdbId: 454109,
            titleSlug: "widows-bay",
            seriesType: "standard",
            seasons: [{ seasonNumber: 1 }],
          },
        ]);
      }
      if (url.pathname.endsWith("/series") && init?.method === "GET") {
        requestedFullLibrary = !url.searchParams.has("tvdbId");
        expect(url.searchParams.get("tvdbId")).toBe("454109");
        return json([]);
      }
      if (url.pathname.endsWith("/rootfolder")) {
        return json([
          { id: 10, path: "G:\\TV", accessible: true },
          { id: 17, path: "G:\\Kids\\TV", accessible: true },
        ]);
      }
      if (url.pathname.endsWith("/qualityprofile")) {
        return json([
          { id: 1, name: "Any" },
          { id: 4, name: "HD-1080p" },
        ]);
      }
      if (url.pathname.endsWith("/series") && init?.method === "POST") {
        posted = true;
        return json({ title: "Widow's Bay", year: 2026, tvdbId: 454109 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    const result = await client.add({
      kind: "series",
      title: "Widow's Bay",
      year: 2026,
      searchNow: true,
    });
    expect(requestedFullLibrary).toBe(false);
    expect(posted).toBe(true);
    expect(result).toMatchObject({ added: true, title: "Widow's Bay", year: 2026 });
  });

  test("does not report success without a matching creation confirmation", async () => {
    const client = new MediaServiceClient(connections, async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/movie/lookup")) {
        return json([{ title: "Arrival", year: 2016, tmdbId: 329865 }]);
      }
      if (url.pathname.endsWith("/movie") && init?.method === "GET") return json([]);
      if (url.pathname.endsWith("/rootfolder")) return json([{ id: 13, path: "G:\\Movies" }]);
      if (url.pathname.endsWith("/qualityprofile")) return json([{ id: 1, name: "Any" }]);
      if (url.pathname.endsWith("/movie") && init?.method === "POST") {
        return json({ title: "Arrival", year: 2016 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    await expect(
      client.add({ kind: "movie", title: "Arrival", year: 2016, searchNow: true }),
    ).rejects.toThrow("did not confirm");
  });

  test("queues searches for only the explicitly selected Sonarr episodes", async () => {
    const calls: Array<{ url: URL; init?: RequestInit }> = [];
    const fetcher: MediaFetcher = async (input, init) => {
      const url = new URL(String(input));
      calls.push({ url, ...(init ? { init } : {}) });
      if (url.pathname.endsWith("/series/lookup")) {
        expect(url.searchParams.get("term")).toBe("Severance");
        return json([{ title: "Severance", year: 2022, tvdbId: 371980 }]);
      }
      if (url.pathname.endsWith("/series")) {
        expect(url.searchParams.get("tvdbId")).toBe("371980");
        return json([{ id: 12, title: "Severance", year: 2022, tvdbId: 371980 }]);
      }
      if (url.pathname.endsWith("/episode")) {
        expect(url.searchParams.get("seriesId")).toBe("12");
        return json([
          { id: 101, seasonNumber: 1, episodeNumber: 2, title: "Half Loop", hasFile: false },
          { id: 109, seasonNumber: 2, episodeNumber: 4, title: "Woe's Hollow", hasFile: true },
        ]);
      }
      if (url.pathname.endsWith("/command") && init?.method === "POST") {
        return json({ id: 77, name: "EpisodeSearch" });
      }
      throw new Error(`Unexpected request: ${url}`);
    };
    const result = await new MediaServiceClient(connections, fetcher).manageSonarrEpisodes({
      action: "search_episodes",
      seriesTitle: "Severance",
      episodes: [
        { seasonNumber: 1, episodeNumber: 2 },
        { seasonNumber: 2, episodeNumber: 4 },
        { seasonNumber: 1, episodeNumber: 2 },
      ],
    });
    const command = calls.find((call) => call.url.pathname.endsWith("/command"));
    expect(JSON.parse(String(command?.init?.body))).toEqual({
      name: "EpisodeSearch",
      episodeIds: [101, 109],
    });
    expect(calls.some((call) => call.url.pathname.endsWith("/series") && !call.url.search)).toBe(
      false,
    );
    expect(result).toMatchObject({
      action: "search_episodes",
      performed: true,
      queued: true,
      episodes: [
        { seasonNumber: 1, episodeNumber: 2 },
        { seasonNumber: 2, episodeNumber: 4 },
      ],
    });
  });

  test("queues one bounded Sonarr season search without a series or library scan", async () => {
    let commandBody: unknown;
    const client = new MediaServiceClient(connections, async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/series/lookup")) {
        return json([{ title: "The Bear", year: 2022, tvdbId: 403294 }]);
      }
      if (url.pathname.endsWith("/series")) {
        expect(url.searchParams.get("tvdbId")).toBe("403294");
        return json([{ id: 5, title: "The Bear", year: 2022, tvdbId: 403294 }]);
      }
      if (url.pathname.endsWith("/episode")) {
        return json([
          { id: 1, seasonNumber: 2, episodeNumber: 1, title: "Beef" },
          { id: 2, seasonNumber: 2, episodeNumber: 2, title: "Pasta" },
          { id: 3, seasonNumber: 3, episodeNumber: 1, title: "Tomorrow" },
        ]);
      }
      if (url.pathname.endsWith("/command") && init?.method === "POST") {
        commandBody = JSON.parse(String(init.body));
        return json({ id: 88, name: "SeasonSearch" });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    const result = await client.manageSonarrEpisodes({
      action: "search_season",
      seriesTitle: "The Bear",
      seasonNumber: 2,
    });
    expect(commandBody).toEqual({ name: "SeasonSearch", seriesId: 5, seasonNumber: 2 });
    expect(result).toMatchObject({
      action: "search_season",
      performed: true,
      queued: true,
      seasonNumber: 2,
      episodeCount: 2,
    });
  });

  test("requires explicit confirmation before deleting a shared multi-episode file", async () => {
    let deleteBody: unknown;
    let deletes = 0;
    const client = new MediaServiceClient(connections, async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/series/lookup")) {
        return json([{ title: "Doctor Who", year: 2005, tvdbId: 78804 }]);
      }
      if (url.pathname.endsWith("/series")) {
        return json([{ id: 9, title: "Doctor Who", year: 2005, tvdbId: 78804 }]);
      }
      if (url.pathname.endsWith("/episode")) {
        return json([
          {
            id: 41,
            seasonNumber: 4,
            episodeNumber: 9,
            title: "Silence in the Library",
            hasFile: true,
            episodeFileId: 500,
          },
          {
            id: 42,
            seasonNumber: 4,
            episodeNumber: 10,
            title: "Forest of the Dead",
            hasFile: true,
            episodeFileId: 500,
          },
        ]);
      }
      if (url.pathname.endsWith("/episodefile/bulk") && init?.method === "DELETE") {
        deletes += 1;
        expect(url.toString()).toBe("http://127.0.0.1:8989/sonarr/api/v3/episodefile/bulk");
        expect(new Headers(init.headers).get("X-Api-Key")).toBe("sonarr-master-key");
        deleteBody = JSON.parse(String(init.body));
        return new Response(null, { status: 200 });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    const target = [{ seasonNumber: 4, episodeNumber: 9 }];
    const preview = await client.manageSonarrEpisodes({
      action: "delete_episode_files",
      seriesTitle: "Doctor Who",
      year: 2005,
      episodes: target,
      allowSharedFiles: false,
    });
    expect(preview).toMatchObject({
      performed: false,
      requiresSharedFileConfirmation: true,
      additionallyAffectedEpisodes: [{ seasonNumber: 4, episodeNumber: 10 }],
    });
    expect(deletes).toBe(0);

    const deleted = await client.manageSonarrEpisodes({
      action: "delete_episode_files",
      seriesTitle: "Doctor Who",
      year: 2005,
      episodes: target,
      allowSharedFiles: true,
    });
    expect(deleteBody).toEqual({ episodeFileIds: [500] });
    expect(deletes).toBe(1);
    expect(deleted).toMatchObject({
      action: "delete_episode_files",
      performed: true,
      deleted: true,
      deletedFileCount: 1,
      additionallyAffectedEpisodes: [{ seasonNumber: 4, episodeNumber: 10 }],
    });
  });

  test("does not partially delete when any selected Sonarr episode has no file", async () => {
    let deleted = false;
    const client = new MediaServiceClient(connections, async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/series/lookup")) {
        return json([{ title: "Andor", year: 2022, tvdbId: 393189 }]);
      }
      if (url.pathname.endsWith("/series")) {
        return json([{ id: 3, title: "Andor", year: 2022, tvdbId: 393189 }]);
      }
      if (url.pathname.endsWith("/episode")) {
        return json([
          {
            id: 1,
            seasonNumber: 1,
            episodeNumber: 1,
            title: "Kassa",
            hasFile: true,
            episodeFileId: 20,
          },
          { id: 2, seasonNumber: 1, episodeNumber: 2, title: "That Would Be Me", hasFile: false },
        ]);
      }
      if (init?.method === "DELETE") deleted = true;
      throw new Error(`Unexpected request: ${url}`);
    });
    const result = await client.manageSonarrEpisodes({
      action: "delete_episode_files",
      seriesTitle: "Andor",
      episodes: [
        { seasonNumber: 1, episodeNumber: 1 },
        { seasonNumber: 1, episodeNumber: 2 },
      ],
      allowSharedFiles: false,
    });
    expect(result).toMatchObject({
      performed: false,
      noFileForEpisodes: [{ seasonNumber: 1, episodeNumber: 2 }],
    });
    expect(deleted).toBe(false);
  });
});
