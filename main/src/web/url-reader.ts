import { lookup } from "node:dns/promises";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { isIP, type LookupFunction } from "node:net";

const MAX_URL_LENGTH = 2_048;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const MAX_TEXT_CHARACTERS = 60_000;
const MAX_REDIRECTS = 5;
const REQUEST_TIMEOUT_MS = 10_000;

const TEXT_CONTENT_TYPES = new Set([
  "application/json",
  "application/ld+json",
  "application/xml",
  "application/xhtml+xml",
  "text/csv",
  "text/html",
  "text/markdown",
  "text/plain",
  "text/xml",
]);

export interface ResolvedAddress {
  address: string;
  family: 4 | 6;
}

interface TransportResponse {
  status: number;
  headers: Record<string, string | string[] | undefined>;
  body: Uint8Array;
}

export type UrlResolver = (hostname: string) => Promise<ResolvedAddress[]>;
export type UrlTransport = (url: URL, address: ResolvedAddress) => Promise<TransportResponse>;

function ipv4Number(address: string): number | null {
  const parts = address.split(".").map(Number);
  if (
    parts.length !== 4 ||
    parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    return null;
  }
  const [first = 0, second = 0, third = 0, fourth = 0] = parts;
  return ((first << 24) | (second << 16) | (third << 8) | fourth) >>> 0;
}

function inIpv4Cidr(value: number, base: string, prefix: number): boolean {
  const baseValue = ipv4Number(base);
  if (baseValue === null) return false;
  const mask = prefix === 0 ? 0 : (0xffff_ffff << (32 - prefix)) >>> 0;
  return (value & mask) === (baseValue & mask);
}

function parseIpv6(address: string): number[] | null {
  const normalized = address.toLowerCase().split("%")[0] ?? "";
  const embeddedIpv4 = normalized.match(/(\d+\.\d+\.\d+\.\d+)$/)?.[1];
  let expanded = normalized;
  if (embeddedIpv4) {
    const value = ipv4Number(embeddedIpv4);
    if (value === null) return null;
    expanded = normalized.replace(
      embeddedIpv4,
      `${((value >>> 16) & 0xffff).toString(16)}:${(value & 0xffff).toString(16)}`,
    );
  }
  if ((expanded.match(/::/g) ?? []).length > 1) return null;
  const [leftRaw, rightRaw] = expanded.split("::");
  const left = leftRaw ? leftRaw.split(":") : [];
  const right = rightRaw ? rightRaw.split(":") : [];
  const omitted = 8 - left.length - right.length;
  if ((expanded.includes("::") && omitted < 1) || (!expanded.includes("::") && omitted !== 0)) {
    return null;
  }
  const parts = [...left, ...Array.from({ length: omitted }, () => "0"), ...right];
  if (parts.length !== 8) return null;
  const words = parts.map((part) => Number.parseInt(part || "0", 16));
  if (words.some((word, index) => !/^[0-9a-f]{1,4}$/i.test(parts[index] ?? "") || word > 0xffff)) {
    return null;
  }
  return words;
}

export function isBlockedAddress(address: string): boolean {
  const family = isIP(address);
  if (family === 4) {
    const value = ipv4Number(address);
    if (value === null) return true;
    return [
      ["0.0.0.0", 8],
      ["10.0.0.0", 8],
      ["100.64.0.0", 10],
      ["127.0.0.0", 8],
      ["169.254.0.0", 16],
      ["172.16.0.0", 12],
      ["192.0.0.0", 24],
      ["192.0.2.0", 24],
      ["192.88.99.0", 24],
      ["192.168.0.0", 16],
      ["198.18.0.0", 15],
      ["198.51.100.0", 24],
      ["203.0.113.0", 24],
      ["224.0.0.0", 4],
      ["240.0.0.0", 4],
    ].some(([base, prefix]) => inIpv4Cidr(value, base as string, prefix as number));
  }
  if (family !== 6) return true;
  const words = parseIpv6(address);
  if (!words) return true;
  const [first = 0, second = 0, third = 0, , , , seventh = 0, eighth = 0] = words;
  if (words.slice(0, 5).every((word) => word === 0) && words[5] === 0xffff) {
    const mapped = ((seventh << 16) | eighth) >>> 0;
    const mappedText = `${mapped >>> 24}.${(mapped >>> 16) & 255}.${(mapped >>> 8) & 255}.${mapped & 255}`;
    return isBlockedAddress(mappedText);
  }
  const unspecifiedOrLoopback = words.slice(0, 7).every((word) => word === 0) && eighth <= 1;
  const uniqueLocal = (first & 0xfe00) === 0xfc00;
  const linkLocal = (first & 0xffc0) === 0xfe80;
  const multicast = (first & 0xff00) === 0xff00;
  const documentation = first === 0x2001 && second === 0x0db8;
  const discardOnly = first === 0x0100 && words.slice(1, 4).every((word) => word === 0);
  const localNat64 = first === 0x0064 && second === 0xff9b && third === 1;
  const globallyRoutable = (first & 0xe000) === 0x2000;
  return (
    unspecifiedOrLoopback ||
    uniqueLocal ||
    linkLocal ||
    multicast ||
    documentation ||
    discardOnly ||
    localNat64 ||
    !globallyRoutable
  );
}

function headerValue(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

const defaultResolver: UrlResolver = async (hostname) => {
  const results = await lookup(hostname, { all: true, verbatim: true });
  return results.map((result) => ({ address: result.address, family: result.family as 4 | 6 }));
};

export function createPinnedLookup(address: ResolvedAddress): LookupFunction {
  return (_hostname, options, callback) => {
    if (options.all) {
      callback(null, [{ address: address.address, family: address.family }]);
      return;
    }
    callback(null, address.address, address.family);
  };
}

const defaultTransport: UrlTransport = (url, address) =>
  new Promise((resolve, reject) => {
    const request = url.protocol === "https:" ? httpsRequest : httpRequest;
    const req = request(
      url,
      {
        method: "GET",
        headers: {
          Accept: "text/html, text/plain, text/markdown, application/json, application/xml;q=0.9",
          "Accept-Encoding": "identity",
          "User-Agent": "FigAi/0.1 (+local URL reader)",
        },
        lookup: createPinnedLookup(address),
      },
      (response) => {
        const declared = Number(headerValue(response.headers["content-length"]));
        if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
          response.destroy(new Error("The page exceeds FigAi's 2 MB download limit."));
          return;
        }
        const chunks: Buffer[] = [];
        let total = 0;
        response.on("data", (chunk: Buffer) => {
          total += chunk.byteLength;
          if (total > MAX_RESPONSE_BYTES) {
            response.destroy(new Error("The page exceeds FigAi's 2 MB download limit."));
            return;
          }
          chunks.push(Buffer.from(chunk));
        });
        response.on("end", () => {
          resolve({
            status: response.statusCode ?? 0,
            headers: response.headers,
            body: Buffer.concat(chunks),
          });
        });
        response.on("error", reject);
      },
    );
    req.setTimeout(REQUEST_TIMEOUT_MS, () => req.destroy(new Error("The page request timed out.")));
    req.on("error", reject);
    req.end();
  });

function decodeEntities(value: string): string {
  const named: Record<string, string> = {
    amp: "&",
    apos: "'",
    gt: ">",
    hellip: "…",
    laquo: "«",
    ldquo: "“",
    lsquo: "‘",
    lt: "<",
    nbsp: " ",
    quot: '"',
    raquo: "»",
    rdquo: "”",
    rsquo: "’",
  };
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (entity, key: string) => {
    if (key.startsWith("#x")) {
      const code = Number.parseInt(key.slice(2), 16);
      return Number.isSafeInteger(code) && code <= 0x10ffff ? String.fromCodePoint(code) : entity;
    }
    if (key.startsWith("#")) {
      const code = Number.parseInt(key.slice(1), 10);
      return Number.isSafeInteger(code) && code <= 0x10ffff ? String.fromCodePoint(code) : entity;
    }
    return named[key.toLowerCase()] ?? entity;
  });
}

function stripTags(value: string): string {
  return decodeEntities(value.replace(/<[^>]*>/g, " "))
    .replace(/\s+/g, " ")
    .trim();
}

function htmlToText(html: string, baseUrl: URL): { title: string | null; text: string } {
  const title = stripTags(html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? "") || null;
  let content = html
    .replace(/<!--[\s\S]*?-->/g, " ")
    .replace(/<(script|style|noscript|svg|template|iframe)\b[^>]*>[\s\S]*?<\/\1>/gi, " ");
  content = content.replace(
    /<a\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>([\s\S]*?)<\/a>/gi,
    (_match, doubleQuoted: string, singleQuoted: string, bare: string, body: string) => {
      const label = stripTags(body);
      const href = doubleQuoted || singleQuoted || bare;
      try {
        const target = new URL(href, baseUrl);
        if (!/^https?:$/.test(target.protocol)) return label;
        return label ? `${label} (${target.toString()})` : target.toString();
      } catch {
        return label;
      }
    },
  );
  content = content
    .replace(/<li\b[^>]*>/gi, "\n- ")
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(
      /<\/(address|article|aside|blockquote|div|dl|fieldset|figure|footer|form|h[1-6]|header|main|nav|ol|p|pre|section|table|tr|ul)>/gi,
      "\n",
    )
    .replace(/<[^>]*>/g, " ");
  const text = decodeEntities(content)
    .replace(/\r/g, "")
    .split("\n")
    .map((line) => line.replace(/[\t ]+/g, " ").trim())
    .filter((line, index, lines) => line || (index > 0 && lines[index - 1]))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return { title, text };
}

function normalizeUrl(raw: string): URL {
  if (raw.length > MAX_URL_LENGTH) throw new Error("That URL is too long.");
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("fetch_url requires an absolute HTTP or HTTPS URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("fetch_url supports only HTTP and HTTPS URLs.");
  }
  if (url.username || url.password) throw new Error("URLs containing credentials are not allowed.");
  if (url.port) throw new Error("fetch_url permits only the standard HTTP and HTTPS ports.");
  url.hash = "";
  return url;
}

function normalizedHostname(url: URL): string {
  return url.hostname
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
    .toLowerCase();
}

export class SafeUrlReader {
  constructor(
    private readonly resolver: UrlResolver = defaultResolver,
    private readonly transport: UrlTransport = defaultTransport,
  ) {}

  async read(rawUrl: string): Promise<Record<string, unknown>> {
    let url = normalizeUrl(rawUrl.trim());
    for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects += 1) {
      const hostname = normalizedHostname(url);
      if (
        hostname === "localhost" ||
        hostname.endsWith(".localhost") ||
        hostname.endsWith(".local") ||
        hostname.endsWith(".internal") ||
        hostname.endsWith(".home.arpa")
      ) {
        throw new Error("Private and local network addresses are not available through fetch_url.");
      }
      const directFamily = isIP(hostname);
      const addresses = directFamily
        ? [{ address: hostname, family: directFamily as 4 | 6 }]
        : await this.resolver(hostname);
      if (!addresses.length || addresses.some((address) => isBlockedAddress(address.address))) {
        throw new Error(
          "Private, reserved, or unresolved network addresses are not available through fetch_url.",
        );
      }
      const address = addresses[0];
      if (!address) throw new Error("The page hostname could not be resolved.");
      const response = await this.transport(url, address);
      if (response.status >= 300 && response.status < 400) {
        const location = headerValue(response.headers.location);
        if (!location) throw new Error("The page returned an invalid redirect.");
        if (redirects === MAX_REDIRECTS) throw new Error("The page redirected too many times.");
        url = normalizeUrl(new URL(location, url).toString());
        continue;
      }
      if (response.status < 200 || response.status >= 300) {
        throw new Error(`The page returned HTTP ${response.status}.`);
      }
      const contentEncoding = headerValue(response.headers["content-encoding"]);
      if (contentEncoding && contentEncoding.toLowerCase() !== "identity") {
        throw new Error("The page returned an unsupported content encoding.");
      }
      const contentTypeHeader = headerValue(response.headers["content-type"]) ?? "";
      const contentType = (contentTypeHeader.split(";", 1)[0] ?? "").trim().toLowerCase();
      if (!TEXT_CONTENT_TYPES.has(contentType)) {
        throw new Error(`fetch_url does not support content type ${contentType || "unknown"}.`);
      }
      let decoded: string;
      try {
        decoded = new TextDecoder("utf-8", { fatal: true }).decode(response.body);
      } catch {
        throw new Error("The page is not valid UTF-8 text.");
      }
      const extracted =
        contentType === "text/html" || contentType === "application/xhtml+xml"
          ? htmlToText(decoded, url)
          : { title: null, text: decoded.trim() };
      if (!extracted.text) throw new Error("The page did not contain readable text.");
      const truncated = extracted.text.length > MAX_TEXT_CHARACTERS;
      return {
        untrusted: true,
        sourceUrl: url.toString(),
        title: extracted.title,
        contentType,
        bytes: response.body.byteLength,
        truncated,
        text: truncated
          ? `${extracted.text.slice(0, MAX_TEXT_CHARACTERS)}\n\n[truncated]`
          : extracted.text,
      };
    }
    throw new Error("The page could not be fetched.");
  }
}
