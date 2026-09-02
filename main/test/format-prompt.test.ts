import { describe, expect, test } from "bun:test";
import { threadToChatMessages } from "../src/agent/openrouter.ts";
import { BASE_SYSTEM_PROMPT, buildSystemPrompt } from "../src/agent/prompt.ts";
import { BRAIN_LIBRARIAN_SKILL } from "../src/db/migrations.ts";
import {
  appendWriteReceiptFooter,
  markdownToMrkdwn,
  removeInternalBrainLinks,
  splitSlackResponse,
} from "../src/slack/format.ts";
import { context } from "./helpers.ts";

describe("prompt and Slack formatting", () => {
  test("system policy contains the required personality and trust boundary", () => {
    expect(BASE_SYSTEM_PROMPT).toContain("lead with the answer");
    expect(BASE_SYSTEM_PROMPT).toContain("unmistakably snarky");
    expect(BASE_SYSTEM_PROMPT).toContain("playful skepticism");
    expect(BASE_SYSTEM_PROMPT).toContain("not at the user's identity");
    expect(BASE_SYSTEM_PROMPT).toContain("Clarity wins");
    expect(BASE_SYSTEM_PROMPT).toContain("Research is a method, not a request for length");
    expect(BASE_SYSTEM_PROMPT).toContain("under 100 words by default");
    expect(BASE_SYSTEM_PROMPT).toContain("user will ask a follow-up");
    expect(BASE_SYSTEM_PROMPT).toContain("untrusted data");
    expect(BASE_SYSTEM_PROMPT).toContain("explicitly asks");
    expect(BASE_SYSTEM_PROMPT).toContain("Typed requester preferences");
    expect(BASE_SYSTEM_PROMPT).toContain("Active temporary directives");
    expect(BASE_SYSTEM_PROMPT).toContain("checked semantically against active directives");
    expect(BASE_SYSTEM_PROMPT).toContain("not a hardcoded assumption");
    expect(BASE_SYSTEM_PROMPT).toContain("can never grant permission");
    expect(BASE_SYSTEM_PROMPT).toContain("Use create_reminder only when");
    expect(BASE_SYSTEM_PROMPT).toContain("known message is the entire future job");
    expect(BASE_SYSTEM_PROMPT).toContain("scheduled temporary directive");
    expect(BASE_SYSTEM_PROMPT).toContain("perform multiple ordered effects");
    expect(BASE_SYSTEM_PROMPT).toContain("one scheduled task, not an immediate directive");
    expect(BASE_SYSTEM_PROMPT).toContain("store its real activation in starts_at");
    expect(BASE_SYSTEM_PROMPT).toContain("call complete_turn_silently");
    expect(BASE_SYSTEM_PROMPT).toContain("new ongoing no-response condition");
    expect(BASE_SYSTEM_PROMPT).toContain("new top-level message in the current channel");
    expect(BASE_SYSTEM_PROMPT).toContain("ask before calling a scheduling tool and never guess");
    expect(BASE_SYSTEM_PROMPT).toContain("scheduled task command must stand alone");
    expect(BASE_SYSTEM_PROMPT).toContain("lower-priority, untrusted operational guidance");
    expect(BASE_SYSTEM_PROMPT).toContain("Never propose and confirm a skill in the same turn");
    expect(BASE_SYSTEM_PROMPT).toContain("it creates a draft and never needs a skill ID");
    expect(BASE_SYSTEM_PROMPT).toContain("never needs a proposal ID");
    expect(BASE_SYSTEM_PROMPT).toContain("brain_export_map");
    expect(BASE_SYSTEM_PROMPT).toContain("never claim a map was attached unless the tool succeeds");
    expect(BASE_SYSTEM_PROMPT).toContain("call get_recent_actions before answering");
    expect(BASE_SYSTEM_PROMPT).toContain("Never deny, invent, or guess about prior tool activity");
    expect(BASE_SYSTEM_PROMPT).toContain("load the enabled Brain Librarian skill");
    expect(BASE_SYSTEM_PROMPT).toContain("Ordinary Brain retrieval does not require it");
    expect(BASE_SYSTEM_PROMPT).toContain("Use fetch_url to read a specific public URL");
    expect(BASE_SYSTEM_PROMPT).toContain("available in any Slack conversation");
    expect(BASE_SYSTEM_PROMPT).toContain("Only the trusted owner may add a movie");
    expect(BASE_SYSTEM_PROMPT).toContain("Only in the owner's DM, manage_sonarr_episodes");
    expect(BASE_SYSTEM_PROMPT).toContain("Never initiate a whole-series or whole-library search");
    expect(BASE_SYSTEM_PROMPT).toContain("report them and do nothing until a later owner message");
    expect(BASE_SYSTEM_PROMPT).toContain("A queued search is not a confirmed download");
    expect(BASE_SYSTEM_PROMPT).toContain("credible first-person or POV evidence");
    expect(BASE_SYSTEM_PROMPT).toContain("a recognizable normal step counts");
    expect(BASE_SYSTEM_PROMPT).toContain("the requester need not be in frame");
    expect(BASE_SYSTEM_PROMPT).toContain("trusted-event completion policy");
    expect(BASE_SYSTEM_PROMPT).toContain("nodes value must be a JSON array");
    expect(BASE_SYSTEM_PROMPT).toContain("never invent agent, process, tool, or condition nodes");
    expect(BASE_SYSTEM_PROMPT).toContain("soft-deleted by the runtime");
    expect(BASE_SYSTEM_PROMPT).toContain("call both list_reminders and list_workflows");
    expect(BASE_SYSTEM_PROMPT).toContain("sentMessages is a delivered-message count");
    expect(BASE_SYSTEM_PROMPT).not.toContain("buy toothpaste");
    expect(BASE_SYSTEM_PROMPT).not.toContain("Never create one file per checkbox");
    const prompt = buildSystemPrompt({
      context: context(),
      memories: [
        {
          id: 1,
          scope_type: "channel",
          scope_id: "C123",
          text: "Prefer terse replies",
          creator_user_id: "U123",
          created_at: 1,
        },
      ],
      preferences: [
        {
          workspace_id: "T123",
          user_id: "U123",
          preference_key: "language",
          preference_value: "Spanish",
          created_at: 1,
          updated_at: 1,
        },
      ],
      directives: [
        {
          id: 2,
          workspace_id: "T123",
          user_id: "U123",
          scope_type: "thread",
          scope_id: "C123:100.000",
          effect: "guidance",
          directive_text: "Use short checkpoints today.",
          release_phrase: null,
          starts_at: 1,
          expires_at: null,
          created_at: 1,
          resolved_at: null,
          resolution: null,
        },
      ],
      releasedDirectives: [
        {
          id: 3,
          workspace_id: "T123",
          user_id: "U123",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "When I finish, remove AppleCare from my To Do list.",
          release_phrase: "the user says AppleCare research is finished",
          starts_at: 1,
          expires_at: null,
          created_at: 1,
          resolved_at: 2,
          resolution: "completed",
        },
      ],
      skills: [
        {
          id: 1,
          name: BRAIN_LIBRARIAN_SKILL.name,
          description: BRAIN_LIBRARIAN_SKILL.description,
          version: 1,
          enabled: true,
        },
        {
          id: 7,
          name: "Release notes",
          description: "Formats concise customer-facing release notes",
          version: 2,
          enabled: true,
        },
      ],
      now: new Date("2026-08-23T12:00:00Z"),
    });
    expect(prompt).toContain("2026-08-23T08:00:00.000-04:00");
    expect(prompt).toContain("You are speaking with: Test User");
    expect(prompt).toContain("Just-satisfied temporary directives");
    expect(prompt).toContain("remove AppleCare from my To Do list");
    expect(prompt).toContain("Never infer that the requester");
    expect(prompt).toContain("without mentioning system prompts");
    expect(prompt).not.toContain("Requester ID:");
    expect(prompt).not.toContain("Workspace ID:");
    expect(prompt).toContain("[1] Prefer terse replies");
    expect(prompt).toContain("language: Spanish");
    expect(prompt).toContain('"instruction":"Use short checkpoints today."');
    expect(prompt).toContain(
      '{"id":7,"name":"Release notes","description":"Formats concise customer-facing release notes"}',
    );
    expect(prompt).toContain(`"name":"${BRAIN_LIBRARIAN_SKILL.name}"`);
    expect(prompt).not.toContain("Make the filing decision");
    expect(prompt).not.toContain("Group changes by customer impact");
  });

  test("keeps root plus newest 79 messages and applies attachments only to invocation", () => {
    const messages = Array.from({ length: 90 }, (_, index) => ({
      ts: `${index + 1}.000`,
      user: index % 2 ? "UBOT" : "U123",
      text: `message ${index + 1}`,
    }));
    const formatted = threadToChatMessages({
      systemPrompt: "system",
      messages,
      botUserId: "UBOT",
      requesterId: "U123",
      requesterName: "Test User",
      invokingTs: "90.000",
      attachmentParts: [{ type: "image_url", image_url: { url: "data:image/gif;base64,abc" } }],
    });
    expect(formatted).toHaveLength(81);
    expect(formatted[1]?.content).toContain("message 1");
    expect(formatted.at(-1)?.content).toBeArray();
  });

  test("strictly caps thread text at 60,000 characters and does not impersonate other bots", () => {
    const formatted = threadToChatMessages({
      systemPrompt: "system",
      messages: [
        { ts: "1", user: "U1", text: "a".repeat(40_000) },
        { ts: "2", user: "U2", bot_id: "BOTHER", text: "b".repeat(40_000) },
      ],
      botUserId: "UBOT",
      requesterId: "U1",
      requesterName: "Test User",
      invokingTs: "2",
    });
    const threadCharacters = formatted
      .slice(1)
      .reduce(
        (sum, message) => sum + (typeof message.content === "string" ? message.content.length : 0),
        0,
      );
    expect(threadCharacters).toBeLessThanOrEqual(60_100);
    expect(formatted[2]?.role).toBe("user");
    expect(formatted[1]?.content).toContain("[Test User]");
    expect(formatted[2]?.content).toContain("[Unknown Slack participant]");
    expect(formatted[1]?.content).not.toContain("U1");
  });

  test("labels every known human participant by resolved Slack name", () => {
    const formatted = threadToChatMessages({
      systemPrompt: "system",
      messages: [
        { ts: "1", user: "U1", text: "hello" },
        { ts: "2", user: "U2", text: "hi Matt" },
      ],
      botUserId: "UBOT",
      requesterId: "U1",
      requesterName: "Matt",
      participantNames: new Map([
        ["U1", "Matt"],
        ["U2", "David"],
      ]),
      invokingTs: "2",
    });
    expect(formatted[1]?.content).toBe("[Matt] hello");
    expect(formatted[2]?.content).toBe("[David] hi Matt");
    expect(JSON.stringify(formatted)).not.toContain("U2");
  });

  test("converts common Markdown and splits at safe boundaries", () => {
    expect(markdownToMrkdwn("## Title\n**bold** [site](https://example.com)")).toBe(
      "*Title*\n*bold* <https://example.com|site>",
    );
    const chunks = splitSlackResponse("word ".repeat(100), 100);
    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks.every((chunk) => chunk.length <= 100)).toBeTrue();
  });

  test("formats compact, deduplicated write receipts without changing read-only replies", () => {
    expect(appendWriteReceiptFooter("Done.", [])).toBe("Done.");
    expect(
      appendWriteReceiptFooter("Done.\n", [
        "Private memory saved",
        "Private   memory saved",
        "Reminder created",
      ]),
    ).toBe("Done.\n\n*✓ Private memory saved · ✓ Reminder created*");
  });

  test("removes internal Brain links from Slack replies while preserving web links", () => {
    expect(
      removeInternalBrainLinks(
        "Saved in [[wiki/lists/to-do|To Do]]. See [source](sources/thought/raw.md), [local](/Users/matt/vault/note.md), [result](brain-ref:1234-abcd), and [docs](https://example.com).",
      ),
    ).toBe("Saved in To Do. See source, local, result, and [docs](https://example.com).");
    expect(removeInternalBrainLinks("Used [[wiki/people/dave]].")).toBe("Used dave.");
    expect(removeInternalBrainLinks("Used `brain-ref:1234-abcd`.")).toBe("Used Brain note.");
  });
});
