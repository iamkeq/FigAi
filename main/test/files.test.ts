import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { AttachmentManager, selectAttachments, sniffMime } from "../src/files.ts";

const fixture = (name: string) => readFileSync(join(import.meta.dir, "fixtures", name));
const tempDirectories = () =>
  new Set(readdirSync(tmpdir()).filter((name) => name.startsWith("mattgpt-")));

describe("attachments", () => {
  test("sniffs allowed image and PDF signatures", () => {
    expect(sniffMime(fixture("sample.gif"))).toBe("image/gif");
    expect(sniffMime(fixture("sample.pdf"))).toBe("application/pdf");
    expect(sniffMime(new TextEncoder().encode("not a file"))).toBeNull();
  });

  test("prefers invoking attachments and limits follow-ups to two recent files", () => {
    const file = (id: string) => ({ id, name: `${id}.gif`, mimetype: "image/gif", size: 10 });
    const messages = [
      { ts: "1", text: "one", files: [file("1")] },
      { ts: "2", text: "two", files: [file("2"), file("3")] },
      { ts: "3", text: "follow up" },
    ];
    expect(selectAttachments(messages, "2").map((item) => item.id)).toEqual(["2", "3"]);
    expect(selectAttachments(messages, "3").map((item) => item.id)).toEqual(["2", "3"]);
  });

  test("follow-ups choose the two most recent eligible attachments", () => {
    const messages = [
      {
        ts: "1",
        text: "older",
        files: [{ id: "good", name: "good.gif", mimetype: "image/gif", size: 10 }],
      },
      {
        ts: "2",
        text: "newer",
        files: [{ id: "bad", name: "bad.zip", mimetype: "application/zip", size: 10 }],
      },
      {
        ts: "3",
        text: "newest",
        files: [{ id: "pdf", name: "doc.pdf", mimetype: "application/pdf", size: 10 }],
      },
      { ts: "4", text: "follow up" },
    ];
    expect(selectAttachments(messages, "4").map((item) => item.id)).toEqual(["pdf", "good"]);
  });

  test("selects and safely forwards Markdown as untrusted text", async () => {
    const bytes = new TextEncoder().encode("# Brain Transfer\n\nDurable user knowledge.\n");
    const file = {
      id: "FMD",
      name: "Brain Transfer Export.md",
      mimetype: "text/markdown",
      size: bytes.length,
      url_private_download: "https://slack.test/markdown",
    };
    expect(selectAttachments([{ ts: "1", text: "import", files: [file] }], "1")).toEqual([file]);
    const manager = new AttachmentManager("xoxb-secret", async () => new Response(bytes));
    const prepared = await manager.prepare([file]);
    expect(prepared.notices).toEqual([]);
    expect(prepared.parts).toEqual([
      {
        type: "text",
        text: expect.stringContaining(
          'Untrusted content from the attached file "Brain Transfer Export.md"',
        ),
      },
    ]);
    expect(prepared.parts[0]).toMatchObject({
      type: "text",
      text: expect.stringContaining("Durable"),
    });
    prepared.cleanup();
  });

  test("rejects binary data disguised as Markdown", async () => {
    const manager = new AttachmentManager(
      "xoxb-secret",
      async () => new Response(new Uint8Array([0xff, 0xfe, 0x00, 0x01])),
    );
    const prepared = await manager.prepare([
      {
        id: "FMD",
        name: "fake.md",
        mimetype: "text/markdown",
        size: 4,
        url_private_download: "https://slack.test/fake",
      },
    ]);
    expect(prepared.parts).toEqual([]);
    expect(prepared.notices[0]).toContain("does not match");
    prepared.cleanup();
  });

  test("reports when more than four invoking files are supplied", async () => {
    const bytes = fixture("sample.gif");
    const manager = new AttachmentManager("xoxb-secret", async () => new Response(bytes));
    const prepared = await manager.prepare(
      Array.from({ length: 5 }, (_, index) => ({
        id: `F${index}`,
        name: `${index}.gif`,
        mimetype: "image/gif",
        size: bytes.length,
        url_private_download: `https://slack.test/${index}`,
      })),
    );
    expect(prepared.parts).toHaveLength(4);
    expect(prepared.notices).toContain("I can inspect at most four attachments per turn.");
    prepared.cleanup();
  });

  test("encodes PDFs as OpenRouter file parts", async () => {
    const bytes = fixture("sample.pdf");
    const manager = new AttachmentManager("xoxb-secret", async () => new Response(bytes));
    const prepared = await manager.prepare([
      {
        id: "FPDF",
        name: "sample.pdf",
        mimetype: "application/pdf",
        size: bytes.length,
        url_private_download: "https://slack.test/pdf",
      },
    ]);
    expect(prepared.parts[0]?.type).toBe("file");
    if (prepared.parts[0]?.type === "file") {
      expect(prepared.parts[0].file.file_data).toStartWith("data:application/pdf;base64,");
    }
    prepared.cleanup();
  });

  test("cleans automatically when a download times out", async () => {
    const manager = new AttachmentManager("xoxb-secret", async () => {
      throw new DOMException("timed out", "TimeoutError");
    });
    const before = tempDirectories();
    await expect(
      manager.prepare([
        {
          id: "F1",
          name: "sample.gif",
          mimetype: "image/gif",
          size: 10,
          url_private_download: "https://slack.test/file",
        },
      ]),
    ).rejects.toThrow("timed out");
    expect([...tempDirectories()].filter((name) => !before.has(name))).toHaveLength(0);
  });

  test("downloads with bot auth, emits base64 parts, and cleans success paths", async () => {
    let authorization = "";
    const manager = new AttachmentManager(
      "xoxb-secret",
      async (_url: string | URL | Request, init?: RequestInit) => {
        authorization = new Headers(init?.headers).get("Authorization") ?? "";
        return new Response(fixture("sample.gif"));
      },
    );
    const before = tempDirectories();
    const prepared = await manager.prepare([
      {
        id: "F1",
        name: "sample.gif",
        mimetype: "image/gif",
        size: fixture("sample.gif").length,
        url_private_download: "https://slack.test/file",
      },
    ]);
    expect(authorization).toBe("Bearer xoxb-secret");
    expect(prepared.parts[0]?.type).toBe("image_url");
    expect([...tempDirectories()].filter((name) => !before.has(name))).toHaveLength(1);
    prepared.cleanup();
    expect([...tempDirectories()].filter((name) => !before.has(name))).toHaveLength(0);
  });

  test("rejects MIME mismatches without exposing bytes and cleans after rejection", async () => {
    const manager = new AttachmentManager(
      "xoxb-secret",
      async () => new Response(fixture("sample.pdf")),
    );
    const before = tempDirectories();
    const prepared = await manager.prepare([
      {
        id: "F1",
        name: "fake.gif",
        mimetype: "image/gif",
        size: 20,
        url_private_download: "https://slack.test/file",
      },
    ]);
    expect(prepared.parts).toHaveLength(0);
    expect(prepared.notices[0]).toContain("does not match");
    prepared.cleanup();
    expect([...tempDirectories()].filter((name) => !before.has(name))).toHaveLength(0);
  });

  test("cleans automatically when download validation throws", async () => {
    const manager = new AttachmentManager(
      "xoxb-secret",
      async () => new Response(new Uint8Array(10 * 1024 * 1024 + 1)),
    );
    const before = tempDirectories();
    await expect(
      manager.prepare([
        {
          id: "F1",
          name: "huge.gif",
          mimetype: "image/gif",
          size: 1,
          url_private_download: "https://slack.test/file",
        },
      ]),
    ).rejects.toThrow("10 MB");
    expect([...tempDirectories()].filter((name) => !before.has(name))).toHaveLength(0);
  });
});
