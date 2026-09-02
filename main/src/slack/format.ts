const SLACK_LIMIT = 3900;

export function appendWriteReceiptFooter(input: string, receipts: string[]): string {
  const unique = [
    ...new Set(receipts.map((receipt) => receipt.replace(/\s+/g, " ").trim()).filter(Boolean)),
  ];
  if (!unique.length) return input;
  return `${input.trimEnd()}\n\n*${unique.map((receipt) => `✓ ${receipt}`).join(" · ")}*`;
}

export function removeInternalBrainLinks(input: string): string {
  return input
    .replace(
      /\[\[((?:wiki|sources|maps)\/[^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]/gi,
      (_match, path: string, label?: string) =>
        label?.trim() || path.split("/").at(-1)?.replace(/-/g, " ") || "Brain note",
    )
    .replace(
      /\[([^\]]+)]\((?:brain-ref:[^)]+|file:\/\/[^)]+|\/Users\/[^)]+|(?:wiki|sources|maps)\/[^)]+)\)/gi,
      "$1",
    )
    .replace(/`?brain-ref:[0-9a-f-]+`?/gi, "Brain note");
}

export function markdownToMrkdwn(input: string): string {
  return input
    .replace(/\[([^\]]+)]\((https?:\/\/[^)]+)\)/g, "<$2|$1>")
    .replace(/^#{1,6}\s+(.+)$/gm, "*$1*")
    .replace(/\*\*([^*]+)\*\*/g, "*$1*")
    .replace(/~~([^~]+)~~/g, "~$1~");
}

export function splitSlackResponse(input: string, limit = SLACK_LIMIT): string[] {
  if (input.length <= limit) return [input];
  const chunks: string[] = [];
  let rest = input;
  while (rest.length > limit) {
    const candidate = rest.slice(0, limit);
    const breakAt = Math.max(
      candidate.lastIndexOf("\n\n"),
      candidate.lastIndexOf("\n"),
      candidate.lastIndexOf(" "),
    );
    const end = breakAt > limit * 0.6 ? breakAt : limit;
    chunks.push(rest.slice(0, end).trimEnd());
    rest = rest.slice(end).trimStart();
  }
  if (rest) chunks.push(rest);
  return chunks;
}
