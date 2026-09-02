import { describe, expect, test } from "bun:test";
import { Agent, AgentRunError, OpenRouterClient, ProviderError } from "../src/agent/openrouter.ts";
import type { ToolExecutor } from "../src/agent/tools.ts";
import type { BrainMapProvider } from "../src/brain/map.ts";
import { ActionJournalRepository } from "../src/db/actions.ts";
import type { UserProfileProvider } from "../src/slack/profiles.ts";
import { context, testDatabase } from "./helpers.ts";

const config = {
  openRouterApiKey: "sk-or-v1-test",
  primaryModel: "primary/model",
  fallbackModel: "fallback/model",
  loadingStatusModel: "loading/model",
  directivePolicyModel: "policy/model",
  imageGenerationModel: "image/model",
};

function completion(message: Record<string, unknown>, model = "primary/model") {
  return new Response(
    JSON.stringify({
      model,
      choices: [{ message: { role: "assistant", ...message } }],
      usage: { prompt_tokens: 2, completion_tokens: 3, total_tokens: 5, cost: 0.01 },
    }),
    { headers: { "Content-Type": "application/json" } },
  );
}

describe("OpenRouter adapter and agent loop", () => {
  test("judges temporary-directive ingress semantically with the policy model", async () => {
    let body: Record<string, unknown> = {};
    const client = new OpenRouterClient(config, async (_url, init) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return completion(
        { content: JSON.stringify({ satisfied_ids: [7, 8, 999], bypass_ids: [8] }) },
        "policy/model",
      );
    });
    const directives = [
      {
        id: 7,
        workspace_id: "T1",
        user_id: "U1",
        scope_type: "global" as const,
        scope_id: "*",
        effect: "guidance" as const,
        directive_text: "Do not reply until the report is finished.",
        release_phrase: "the user indicates the report is finished",
        starts_at: 1,
        expires_at: null,
        created_at: 1,
        resolved_at: null,
        resolution: null,
      },
      {
        id: 8,
        workspace_id: "T1",
        user_id: "U1",
        scope_type: "global" as const,
        scope_id: "*",
        effect: "guidance" as const,
        directive_text: "Use concise replies for one hour.",
        release_phrase: null,
        starts_at: 1,
        expires_at: 2,
        created_at: 1,
        resolved_at: null,
        resolution: null,
      },
    ];

    expect(await client.evaluateDirectiveIngress(directives, "I submitted the report")).toEqual({
      satisfiedIds: [7],
      bypassIds: [8],
      outcome: "evaluated",
      reasonCode: "judge_decision",
    });
    expect(body.model).toBe("policy/model");
    expect(body.temperature).toBeUndefined();
    expect(body.reasoning).toEqual({ effort: "low", exclude: true });
    expect(body.response_format).toMatchObject({
      type: "json_schema",
      json_schema: { name: "temporary_directive_release", strict: true },
    });
    expect(body.provider).toEqual({ require_parameters: true });
    expect(JSON.stringify(body.messages)).toContain("exact wording is not required");
    expect(JSON.stringify(body.messages)).toContain("I submitted the report");
  });

  test("leaves directives active when release judgment fails", async () => {
    const client = new OpenRouterClient(config, async () => new Response("bad", { status: 500 }));
    const directive = {
      id: 7,
      workspace_id: "T1",
      user_id: "U1",
      scope_type: "global" as const,
      scope_id: "*",
      effect: "guidance" as const,
      directive_text: "Do not reply until the report is finished.",
      release_phrase: "the user indicates the report is finished",
      starts_at: 1,
      expires_at: null,
      created_at: 1,
      resolved_at: null,
      resolution: null,
    };
    expect(await client.evaluateDirectiveIngress([directive], "done")).toMatchObject({
      satisfiedIds: [],
      bypassIds: [],
      outcome: "unavailable",
    });
  });

  test("judges workflow text and photographic evidence with a narrow multimodal policy call", async () => {
    let body: Record<string, unknown> = {};
    const client = new OpenRouterClient(config, async (_url, init) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return completion(
        {
          content: JSON.stringify({
            matches: [
              { workflow_id: 4, match_index: 0 },
              { workflow_id: 4, match_index: 1 },
              { workflow_id: 999, match_index: 0 },
            ],
            cancel_ids: [9, 999],
          }),
        },
        "policy/model",
      );
    });
    const result = await client.evaluateWorkflowIngress({
      candidates: [
        {
          workflowId: 4,
          name: "Trimmer proof",
          matches: [
            { index: 0, condition: "The image visibly shows a hair trimmer.", evidence: "image" },
            { index: 1, condition: "The user says the task is done.", evidence: "text" },
          ],
        },
        {
          workflowId: 9,
          name: "Daily plan",
          matches: [{ index: 0, condition: "The user provides planned tasks.", evidence: "text" }],
        },
      ],
      message: "Here is the proof.",
      attachmentParts: [
        { type: "image_url", image_url: { url: "data:image/png;base64,aW1hZ2U=" } },
      ],
      files: [{ id: "F1", name: "proof.png", mimetype: "image/png", size: 5 }],
    });
    expect(result).toEqual({
      matches: [{ workflowId: 4, matchIndex: 0 }],
      cancelIds: [9],
      outcome: "evaluated",
      reasonCode: "judge_decision",
    });
    expect(body.model).toBe("policy/model");
    expect(body.reasoning).toEqual({ effort: "low", exclude: true });
    expect(body.response_format).toMatchObject({
      type: "json_schema",
      json_schema: { name: "workflow_event_match", strict: true },
    });
    expect(JSON.stringify(body.messages)).toContain("data:image/png;base64,aW1hZ2U=");
    expect(JSON.stringify(body.messages)).toContain("mere attachment presence is insufficient");
    expect(JSON.stringify(body.messages)).toContain("a credible POV image");
    expect(JSON.stringify(body.messages)).toContain("recognizable intermediate step");
    expect(JSON.stringify(body.messages)).toContain("does not require visual identification");
    expect(JSON.stringify(body.messages)).toContain("only when the condition specifically says");
  });

  test("rejects a claimed image-condition match when no validated image was supplied", async () => {
    const client = new OpenRouterClient(config, async () =>
      completion({
        content: JSON.stringify({
          matches: [{ workflow_id: 4, match_index: 0 }],
          cancel_ids: [],
        }),
      }),
    );
    expect(
      await client.evaluateWorkflowIngress({
        candidates: [
          {
            workflowId: 4,
            name: "Proof",
            matches: [{ index: 0, condition: "Image shows a trimmer.", evidence: "image" }],
          },
        ],
        message: "done",
        attachmentParts: [],
        files: [],
      }),
    ).toMatchObject({ matches: [], cancelIds: [], outcome: "evaluated" });
  });

  test("compiles free-form directives into strict authoritative policies", async () => {
    let body: Record<string, unknown> = {};
    const client = new OpenRouterClient(config, async (_url, init) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return completion(
        {
          content: JSON.stringify({
            version: 1,
            kind: "delivery_suppression",
            delivery: "suppress",
            tools: "block_all",
            requirements: ["Do not send a Slack response while active."],
            summary: "Remain silent until the homework is credibly finished",
          }),
        },
        "policy/model",
      );
    });

    expect(
      await client.compileDirectivePolicy({
        instruction: "Ignore me until I finish my homework.",
        releaseCondition: "the user credibly indicates the homework is finished",
      }),
    ).toMatchObject({
      kind: "delivery_suppression",
      delivery: "suppress",
      tools: "block_all",
    });
    expect(body.model).toBe("policy/model");
    expect(body.provider).toEqual({ require_parameters: true });
    expect(body.response_format).toMatchObject({
      type: "json_schema",
      json_schema: { name: "temporary_directive_policy", strict: true },
    });
    expect(JSON.stringify(body.messages)).toContain("ordinary conversational meaning");
  });

  test("judges proposed behavior against active directives semantically", async () => {
    let body: Record<string, unknown> = {};
    const client = new OpenRouterClient(config, async (_url, init) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return completion(
        { content: JSON.stringify({ decision: "retry", violated_rules: ["response.language"] }) },
        "policy/model",
      );
    });
    const action = await client.evaluateDirectiveCompliance({
      directives: [
        {
          id: 8,
          workspace_id: "T1",
          user_id: "U1",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "Reply only in Spanish for the next hour.",
          release_phrase: null,
          starts_at: 1,
          expires_at: 2,
          created_at: 1,
          resolved_at: null,
          resolution: null,
        },
      ],
      requesterMessage: "How are you?",
      proposedContent: "I am doing well.",
      proposedToolCalls: [],
    });
    expect(action).toMatchObject({
      action: "retry",
      outcome: "evaluated",
      violatedRules: ["response.language"],
    });
    expect(body.model).toBe("policy/model");
    expect(body.temperature).toBeUndefined();
    expect(body.reasoning).toEqual({ effort: "low", exclude: true });
    expect(body.response_format).toMatchObject({
      type: "json_schema",
      json_schema: { name: "temporary_directive_compliance", strict: true },
    });
    expect(JSON.stringify(body.messages)).toContain("may require silence, restrict topics");
    expect(JSON.stringify(body.messages)).toContain("Reply only in Spanish");
  });

  test("fails closed when directive compliance judgment is unavailable", async () => {
    const client = new OpenRouterClient(
      config,
      async () => new Response("unavailable", { status: 500 }),
    );
    expect(
      await client.evaluateDirectiveCompliance({
        directives: [
          {
            id: 8,
            workspace_id: "T1",
            user_id: "U1",
            scope_type: "global",
            scope_id: "*",
            effect: "guidance",
            directive_text: "Use concise replies.",
            release_phrase: null,
            starts_at: 1,
            expires_at: null,
            created_at: 1,
            resolved_at: null,
            resolution: null,
          },
        ],
        requesterMessage: "Hello",
        proposedContent: "Hello.",
        proposedToolCalls: [],
      }),
    ).toMatchObject({ action: "suppress", outcome: "unavailable", reasonCode: "http_500" });
  });

  test("generates a short loading status with the cheap fallback model", async () => {
    let body: Record<string, unknown> = {};
    const client = new OpenRouterClient(config, async (_url, init) => {
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return completion(
        {
          content:
            "FigAi is reading the spreadsheet request\nFigAi is checking the spreadsheet totals\nFigAi is thinking through the corrections\nFigAi is writing the corrected workbook\nFigAi is finishing the spreadsheet",
        },
        "loading/model",
      );
    });
    expect(await client.generateLoadingStatuses("<@UBOT> fix this spreadsheet")).toEqual([
      "is reading the spreadsheet request",
      "is checking the spreadsheet totals",
      "is thinking through the corrections",
      "is writing the corrected workbook",
      "is finishing the spreadsheet",
    ]);
    expect(body.model).toBe("loading/model");
    expect(body.max_tokens).toBe(120);
    expect(body.reasoning).toEqual({ effort: "none", exclude: true });
    expect(body).not.toHaveProperty("tools");
    expect(JSON.stringify(body.messages)).toContain("sharp coworker with taste");
    expect(JSON.stringify(body.messages)).toContain("chronological mini-story");
    expect(JSON.stringify(body.messages)).toContain("Avoid bland status language");
    expect(JSON.stringify(body.messages)).toContain("second word an -ing action verb");
    expect(JSON.stringify(body.messages)).toContain("Never write 'is it'");
    expect(JSON.stringify(body.messages)).not.toContain("UBOT");
  });

  test("rejects bland loading statuses", async () => {
    const client = new OpenRouterClient(config, async () =>
      completion({ content: "is processing your request" }, "fallback/model"),
    );
    expect(await client.generateLoadingStatuses("do something")).toEqual([]);
  });

  test("rejects incomplete loading statuses", async () => {
    const client = new OpenRouterClient(config, async () =>
      completion({ content: "is aggressively color-coding your" }, "fallback/model"),
    );
    expect(await client.generateLoadingStatuses("fix my spreadsheet")).toEqual([]);
  });

  test("rejects question-shaped or grammatically broken loading statuses", async () => {
    const broken = new OpenRouterClient(config, async () =>
      completion(
        {
          content: [
            "is it reporting what's ready",
            "is inspecting the download queue",
            "is checking the suspiciously quiet details",
            "is assembling the useful answer",
            "is polishing the queue report",
          ].join("\n"),
        },
        "loading/model",
      ),
    );
    expect(await broken.generateLoadingStatuses("anything in my downloads?")).toEqual([]);

    const question = new OpenRouterClient(config, async () =>
      completion(
        {
          content: [
            "is opening the download queue?",
            "is inspecting the waiting files",
            "is checking the inconvenient details",
            "is assembling the useful answer",
            "is polishing the queue report",
          ].join("\n"),
        },
        "loading/model",
      ),
    );
    expect(await question.generateLoadingStatuses("anything in my downloads?")).toEqual([]);
  });

  test("rejects loading stages that exceed Slack's 50-character limit", async () => {
    const client = new OpenRouterClient(config, async () =>
      completion(
        {
          content: [
            "is opening the spreadsheet",
            "is inspecting the suspicious totals",
            "is checking every inconvenient formula",
            "is assembling the corrected workbook",
            "is polishing an extraordinarily overcomplicated spreadsheet for management",
          ].join("\n"),
        },
        "fallback/model",
      ),
    );
    expect(await client.generateLoadingStatuses("fix my spreadsheet")).toEqual([]);
  });

  test("uses configured primary then retries one transient failure on fallback", async () => {
    const models: string[] = [];
    const fetcher = async (_url: string | URL | Request, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body)) as {
        model: string;
        parallel_tool_calls: boolean;
        tools: unknown[];
      };
      models.push(body.model);
      expect(body.parallel_tool_calls).toBeFalse();
      expect(body.tools).toContainEqual({
        type: "openrouter:web_search",
        parameters: { engine: "exa", max_results: 8, max_total_results: 8 },
      });
      return models.length === 1
        ? new Response("busy", { status: 503 })
        : completion({ content: "ok" }, "fallback/model");
    };
    const result = await new OpenRouterClient(config, fetcher).completeWithFallback([
      { role: "user", content: "hello" },
    ]);
    expect(models).toEqual(["primary/model", "fallback/model"]);
    expect(result.model).toBe("fallback/model");
  });

  test("recovers from a permanent primary-model failure using the fallback", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? new Response("bad request", { status: 400 })
        : completion({ content: "recovered" }, "fallback/model");
    });
    const result = await client.completeWithFallback([{ role: "user", content: "hello" }]);
    expect(result.message.content).toBe("recovered");
    expect(calls).toBe(2);
  });

  test("validates model IDs against the catalog and resolves private aliases", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async (url) => {
      calls += 1;
      expect(String(url)).toBe("https://openrouter.ai/api/v1/models");
      return new Response(
        JSON.stringify({ data: [{ id: "~deepseek/deepseek-v4-flash-latest" }] }),
        { headers: { "Content-Type": "application/json" } },
      );
    });
    expect(await client.resolveModel("deepseek/deepseek-v4-flash-latest")).toBe(
      "~deepseek/deepseek-v4-flash-latest",
    );
    expect(await client.resolveModel("missing/model")).toBeNull();
    expect(calls).toBe(1);
  });

  test("passes image and PDF content parts through the typed transport", async () => {
    let sentMessages: unknown;
    const client = new OpenRouterClient(config, async (_url, init) => {
      sentMessages = (JSON.parse(String(init?.body)) as { messages: unknown }).messages;
      return completion({ content: "seen" });
    });
    await client.complete(
      [
        {
          role: "user",
          content: [
            { type: "text", text: "inspect" },
            { type: "image_url", image_url: { url: "data:image/gif;base64,R0lG" } },
            {
              type: "file",
              file: { filename: "sample.pdf", file_data: "data:application/pdf;base64,JVBERg==" },
            },
          ],
        },
      ],
      "primary/model",
    );
    expect(JSON.stringify(sentMessages)).toContain("data:image/gif;base64");
    expect(JSON.stringify(sentMessages)).toContain("data:application/pdf;base64");
  });

  test("generates an image with the configured image model", async () => {
    let url = "";
    let body: Record<string, unknown> = {};
    const client = new OpenRouterClient(config, async (input, init) => {
      url = String(input);
      body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(
        JSON.stringify({
          data: [
            { b64_json: Buffer.from("image bytes").toString("base64"), media_type: "image/png" },
          ],
          usage: { cost: 0.004 },
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    });
    const result = await client.generateImage("A tiny orange robot", "4:3");
    expect(url).toBe("https://openrouter.ai/api/v1/images");
    expect(body).toMatchObject({
      model: "image/model",
      prompt: "A tiny orange robot",
      n: 1,
      resolution: "1K",
      aspect_ratio: "4:3",
    });
    expect(Buffer.from(result.image.bytes).toString()).toBe("image bytes");
    expect(result.image.mediaType).toBe("image/png");
    expect(result.reportedCost).toBe(0.004);
  });

  test("executes image generation once and returns it with the final answer", async () => {
    let chatCalls = 0;
    let imageCalls = 0;
    const client = new OpenRouterClient(config, async (input) => {
      if (String(input).endsWith("/images")) {
        imageCalls += 1;
        return new Response(
          JSON.stringify({
            data: [{ b64_json: Buffer.from("png").toString("base64"), media_type: "image/png" }],
            usage: { cost: 0.02 },
          }),
        );
      }
      chatCalls += 1;
      return chatCalls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "image-1",
                type: "function",
                function: {
                  name: "generate_image",
                  arguments: JSON.stringify({ prompt: "A tiny orange robot", aspect_ratio: "1:1" }),
                },
              },
            ],
          })
        : completion({ content: "Here it is." });
    });
    const executor = { execute: () => undefined } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Generate an image of a tiny orange robot" }],
      context: context(),
    });
    expect(imageCalls).toBe(1);
    expect(result.text).toBe("Here it is.");
    expect(result.images).toHaveLength(1);
    expect(result.tools).toContain("generate_image");
    expect(result.reportedCost).toBeCloseTo(0.04);
  });

  test("exports a scoped Brain map and forwards only safe metadata to the model", async () => {
    let chatCalls = 0;
    let followupMessages: unknown;
    const client = new OpenRouterClient(config, async (_input, init) => {
      chatCalls += 1;
      if (chatCalls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "brain-map-1",
              type: "function",
              function: { name: "brain_export_map", arguments: "{}" },
            },
          ],
        });
      }
      followupMessages = (JSON.parse(String(init?.body)) as { messages: unknown }).messages;
      return completion({ content: "Attached your Brain map." });
    });
    const executor = { execute: () => undefined } as unknown as ToolExecutor;
    const brainMaps: BrainMapProvider = {
      exportMap: () => ({
        bytes: Buffer.from("private image bytes"),
        mediaType: "image/png",
        filename: "brain-map.png",
        title: "Brain map",
        altText: "Brain map with 8 notes and 3 relationships across 2 Brains.",
        brainCount: 2,
        nodeCount: 8,
        edgeCount: 3,
      }),
    };

    const result = await new Agent(client, executor, undefined, brainMaps).run({
      messages: [{ role: "user", content: "Export my Brain map" }],
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm" }),
    });

    expect(result.text).toBe("Attached your Brain map.");
    expect(result.tools).toContain("brain_export_map");
    expect(result.images).toEqual([
      {
        bytes: Buffer.from("private image bytes"),
        mediaType: "image/png",
        filename: "brain-map.png",
        title: "Brain map",
        altText: "Brain map with 8 notes and 3 relationships across 2 Brains.",
      },
    ]);
    expect(JSON.stringify(followupMessages)).toContain("brainCount");
    expect(JSON.stringify(followupMessages)).toContain("nodeCount");
    expect(JSON.stringify(followupMessages)).not.toContain("private image bytes");
    expect(JSON.stringify(followupMessages)).not.toContain("base64");
  });

  test("forwards a requested avatar as multimodal input and cleans it after success", async () => {
    let calls = 0;
    let followupMessages: unknown;
    let profileInput: Parameters<UserProfileProvider["getUserProfile"]>[0] | undefined;
    let cleanups = 0;
    const client = new OpenRouterClient(config, async (_input, init) => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "profile-1",
              type: "function",
              function: {
                name: "get_user_profile",
                arguments: JSON.stringify({ user_name: "David", include_avatar: true }),
              },
            },
          ],
        });
      }
      followupMessages = (JSON.parse(String(init?.body)) as { messages: unknown }).messages;
      return completion({ content: "Your avatar is a tiny orange robot." });
    });
    const profiles: UserProfileProvider = {
      getUserProfile: async (input) => {
        profileInput = input;
        return {
          profile: {
            displayName: "Test User",
            realName: "Test Person",
            title: null,
            timezone: "America/New_York",
            statusText: null,
          },
          avatarPart: {
            type: "image_url",
            image_url: { url: "data:image/png;base64,iVBORw0KGgo=" },
          },
          cleanup: () => {
            cleanups += 1;
          },
        };
      },
    };
    const executor = { execute: () => undefined } as unknown as ToolExecutor;
    const result = await new Agent(client, executor, profiles).run({
      messages: [{ role: "user", content: "Look at David's profile picture" }],
      context: context({
        requesterId: "UREQUESTER",
        participantNames: new Map([
          ["UREQUESTER", "Matt"],
          ["UPARTICIPANT", "David"],
        ]),
      }),
    });
    expect(profileInput?.userId).toBe("UPARTICIPANT");
    expect(profileInput?.includeAvatar).toBeTrue();
    expect(JSON.stringify(followupMessages)).toContain("data:image/png;base64,iVBORw0KGgo=");
    const profileToolMessage = (
      followupMessages as Array<{ role?: string; name?: string; content?: string }>
    ).find((message) => message.role === "tool" && message.name === "get_user_profile");
    expect(JSON.parse(profileToolMessage?.content ?? "{}").result.avatarSupplied).toBeTrue();
    expect(JSON.stringify(followupMessages)).toContain("untrusted data");
    expect(result.tools).toContain("get_user_profile");
    expect(cleanups).toBe(1);
  });

  test("cleans a prepared avatar when the following provider call fails", async () => {
    let calls = 0;
    let cleanups = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "profile-1",
              type: "function",
              function: {
                name: "get_user_profile",
                arguments: JSON.stringify({ include_avatar: true }),
              },
            },
          ],
        });
      }
      return new Response("bad request", { status: 400 });
    });
    const profiles: UserProfileProvider = {
      getUserProfile: async () => ({
        profile: {
          displayName: "Test User",
          realName: null,
          title: null,
          timezone: null,
          statusText: null,
        },
        avatarPart: {
          type: "image_url",
          image_url: { url: "data:image/png;base64,iVBORw0KGgo=" },
        },
        cleanup: () => {
          cleanups += 1;
        },
      }),
    };
    const executor = { execute: () => undefined } as unknown as ToolExecutor;
    await expect(
      new Agent(client, executor, profiles).run({
        messages: [{ role: "user", content: "Describe my avatar" }],
        context: context({ requesterId: "UREQUESTER" }),
      }),
    ).rejects.toBeInstanceOf(ProviderError);
    expect(cleanups).toBe(1);
  });

  test("executes local tools sequentially and retains URL citations", async () => {
    let calls = 0;
    const executed: string[] = [];
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            { id: "1", type: "function", function: { name: "list_memories", arguments: "{}" } },
            { id: "2", type: "function", function: { name: "list_reminders", arguments: "{}" } },
          ],
        });
      }
      return completion({
        content: "The result.",
        annotations: [
          {
            type: "url_citation",
            url_citation: { url: "https://example.com/source", title: "Source" },
          },
        ],
      });
    });
    const executor = {
      execute(name: string) {
        executed.push(name);
        return [];
      },
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "research it and include sources" }],
      context: context(),
    });
    expect(executed).toEqual(["list_memories", "list_reminders"]);
    expect(result.text).toContain("[Source](https://example.com/source)");
    expect(result.totalTokens).toBe(10);
  });

  test("returns an explicit silent-success outcome for scheduled tasks", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "fetch-page",
              type: "function",
              function: {
                name: "fetch_url",
                arguments: JSON.stringify({ url: "https://example.com/models" }),
              },
            },
          ],
        });
      }
      return completion({
        content: null,
        tool_calls: [
          {
            id: "silent-success",
            type: "function",
            function: { name: "complete_scheduled_task_silently", arguments: "{}" },
          },
        ],
      });
    });
    const executor = {
      execute: () => ({ untrusted: true, text: "Ox Alpha" }),
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "If only Ox Alpha is listed, say nothing." }],
      context: context({ turnId: "scheduled-task:14:100" }),
    });
    expect(calls).toBe(2);
    expect(result).toMatchObject({
      text: "",
      suppressDelivery: true,
      tools: ["fetch_url", "complete_scheduled_task_silently"],
    });
    expect(result.writeReceipts).toEqual([]);
  });

  test("rejects silent completion during normal Slack turns", async () => {
    let calls = 0;
    let followupBody = "";
    const client = new OpenRouterClient(config, async (_url, init) => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "invalid-silence",
              type: "function",
              function: { name: "complete_scheduled_task_silently", arguments: "{}" },
            },
          ],
        });
      }
      followupBody = String(init?.body);
      return completion({ content: "I can’t suppress an ordinary reply." });
    });
    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "Say nothing" }],
      context: context({ turnId: "Ev-normal" }),
    });
    expect(result.suppressDelivery).toBeUndefined();
    expect(result.text).toBe("I can’t suppress an ordinary reply.");
    expect(followupBody).toContain("available only during scheduled tasks");
  });

  test("allows an active temporary directive to complete a normal turn silently", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "silent-turn",
                type: "function",
                function: { name: "complete_turn_silently", arguments: "{}" },
              },
            ],
          })
        : completion(
            { content: JSON.stringify({ decision: "allow", violated_rules: [] }) },
            "policy/model",
          );
    });
    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "[Test User] hello?" }],
      context: context({ turnId: "Ev-normal" }),
      directives: [
        {
          id: 7,
          workspace_id: "T1",
          user_id: "U1",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "Do not respond until the user says the report is finished.",
          release_phrase: "the user indicates the report is finished",
          starts_at: 1,
          expires_at: null,
          created_at: 1,
          resolved_at: null,
          resolution: null,
        },
      ],
    });
    expect(result).toMatchObject({
      text: "",
      suppressDelivery: true,
      tools: ["directive_compliance", "complete_turn_silently"],
    });
  });

  test("enforces compiled delivery suppression without calling the main model", async () => {
    let providerCalls = 0;
    const client = new OpenRouterClient(config, async () => {
      providerCalls += 1;
      return completion({ content: "This must never be generated." });
    });
    const policy = {
      version: 1,
      kind: "delivery_suppression",
      delivery: "suppress",
      tools: "block_all",
      requirements: ["Do not send a Slack response while active."],
      summary: "Remain silent",
    } as const;
    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "[Test User] hello?" }],
      context: context({ turnId: "Ev-compiled-silence" }),
      directives: [
        {
          id: 70,
          workspace_id: "T1",
          user_id: "U1",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "Ignore me for one minute.",
          release_phrase: null,
          starts_at: 1,
          expires_at: Date.now() + 60_000,
          created_at: 1,
          resolved_at: null,
          resolution: null,
          policy_json: JSON.stringify(policy),
          policy_version: 1,
          policy_compiled_at: 1,
        },
      ],
    });

    expect(providerCalls).toBe(0);
    expect(result).toMatchObject({
      text: "",
      suppressDelivery: true,
      tools: ["directive_compliance"],
    });
  });

  test("honors a compiled suppression policy that becomes active while a turn is running", async () => {
    let providerCalls = 0;
    let directiveReads = 0;
    const client = new OpenRouterClient(config, async () => {
      providerCalls += 1;
      return completion({ content: "This draft must not be delivered." });
    });
    const policy = {
      version: 1,
      kind: "delivery_suppression",
      delivery: "suppress",
      tools: "block_all",
      requirements: ["Do not send a Slack response while active."],
      summary: "Remain silent",
    } as const;
    const directive = {
      id: 71,
      workspace_id: "T1",
      user_id: "U1",
      scope_type: "global" as const,
      scope_id: "*",
      effect: "guidance" as const,
      directive_text: "Begin ignoring me now.",
      release_phrase: null,
      starts_at: 1,
      expires_at: Date.now() + 60_000,
      created_at: 1,
      resolved_at: null,
      resolution: null,
      policy_json: JSON.stringify(policy),
      policy_version: 1,
      policy_compiled_at: 1,
    };

    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "[Test User] hello?" }],
      context: context({ turnId: "Ev-policy-started-mid-turn" }),
      currentDirectives: () => (directiveReads++ === 0 ? [] : [directive]),
    });

    expect(providerCalls).toBe(1);
    expect(result).toMatchObject({
      text: "",
      suppressDelivery: true,
      tools: ["directive_compliance"],
    });
  });

  test("semantically suppresses a violating reply while an active directive applies", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({ content: "Hi. The silence window has expired." })
        : completion(
            { content: JSON.stringify({ decision: "suppress", violated_rules: ["delivery"] }) },
            "policy/model",
          );
    });
    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "[Test User] hi" }],
      context: context({ turnId: "Ev-active-silence" }),
      directives: [
        {
          id: 9,
          workspace_id: "T1",
          user_id: "U1",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "Do not respond during the active one-minute window.",
          release_phrase: null,
          starts_at: 1,
          expires_at: Date.now() + 60_000,
          created_at: 1,
          resolved_at: null,
          resolution: null,
        },
      ],
    });
    expect(calls).toBe(2);
    expect(result).toMatchObject({
      text: "",
      suppressDelivery: true,
      tools: ["directive_compliance"],
    });
  });

  test("regenerates non-silence directive violations before delivery", async () => {
    let calls = 0;
    let retryBody = "";
    const client = new OpenRouterClient(config, async (_url, init) => {
      calls += 1;
      if (calls === 1) return completion({ content: "I am doing well." });
      if (calls === 2) {
        return completion(
          {
            content: JSON.stringify({
              decision: "retry",
              violated_rules: ["response.language"],
            }),
          },
          "policy/model",
        );
      }
      if (calls === 3) {
        retryBody = String(init?.body);
        return completion({ content: "Estoy bien." });
      }
      return completion(
        { content: JSON.stringify({ decision: "allow", violated_rules: [] }) },
        "policy/model",
      );
    });
    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "[Test User] How are you?" }],
      context: context({ turnId: "Ev-active-language" }),
      directives: [
        {
          id: 10,
          workspace_id: "T1",
          user_id: "U1",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "Reply only in Spanish.",
          release_phrase: null,
          starts_at: 1,
          expires_at: Date.now() + 60_000,
          created_at: 1,
          resolved_at: null,
          resolution: null,
        },
      ],
    });
    expect(calls).toBe(4);
    expect(result.text).toBe("Estoy bien.");
    expect(result.tools).toEqual(["directive_compliance"]);
    expect(retryBody).toContain("trusted runtime compliance check rejected");
    expect(retryBody).not.toContain("I am doing well.");
  });

  test("blocks violating tool calls before they execute", async () => {
    let providerCalls = 0;
    let toolCalls = 0;
    const client = new OpenRouterClient(config, async () => {
      providerCalls += 1;
      return providerCalls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "wrong-write",
                type: "function",
                function: {
                  name: "save_memory",
                  arguments: JSON.stringify({ scope: "user", text: "hi" }),
                },
              },
            ],
          })
        : completion(
            { content: JSON.stringify({ decision: "suppress", violated_rules: ["tool.write"] }) },
            "policy/model",
          );
    });
    const executor = {
      execute: () => {
        toolCalls += 1;
        return { saved: true };
      },
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "[Test User] hi" }],
      context: context({ turnId: "Ev-blocked-tool" }),
      directives: [
        {
          id: 11,
          workspace_id: "T1",
          user_id: "U1",
          scope_type: "global",
          scope_id: "*",
          effect: "guidance",
          directive_text: "Do not respond or take actions during this window.",
          release_phrase: null,
          starts_at: 1,
          expires_at: Date.now() + 60_000,
          created_at: 1,
          resolved_at: null,
          resolution: null,
        },
      ],
    });
    expect(toolCalls).toBe(0);
    expect(result).toMatchObject({
      text: "",
      suppressDelivery: true,
      tools: ["directive_compliance"],
    });
  });

  test("rejects normal-turn silence without an explicit requester instruction", async () => {
    let calls = 0;
    let followupBody = "";
    const client = new OpenRouterClient(config, async (_url, init) => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "unjustified-silence",
              type: "function",
              function: { name: "complete_turn_silently", arguments: "{}" },
            },
          ],
        });
      }
      followupBody = String(init?.body);
      return completion({ content: "I should answer this." });
    });
    const result = await new Agent(client, {} as ToolExecutor).run({
      messages: [{ role: "user", content: "[Test User] hello?" }],
      context: context({ turnId: "Ev-normal" }),
    });
    expect(result.suppressDelivery).toBeUndefined();
    expect(result.text).toBe("I should answer this.");
    expect(followupBody).toContain("did not explicitly request silence");
  });

  test("derives safe receipts from successful persistent tool results", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "brain-save",
                type: "function",
                function: {
                  name: "brain_save",
                  arguments: JSON.stringify({ destination_title: "To Do" }),
                },
              },
              {
                id: "reminder-create",
                type: "function",
                function: { name: "create_reminder", arguments: "{}" },
              },
              {
                id: "brain-remove",
                type: "function",
                function: {
                  name: "brain_remove_list_item",
                  arguments: JSON.stringify({
                    destination_title: "To Do",
                    text: "Look into AppleCare",
                  }),
                },
              },
              {
                id: "media-add",
                type: "function",
                function: {
                  name: "add_media",
                  arguments: JSON.stringify({ kind: "movie", title: "Arrival" }),
                },
              },
              {
                id: "sonarr-search",
                type: "function",
                function: {
                  name: "manage_sonarr_episodes",
                  arguments: JSON.stringify({
                    action: "search_episodes",
                    series_title: "Severance",
                    episodes: [{ season_number: 2, episode_number: 4 }],
                  }),
                },
              },
            ],
          })
        : completion({ content: "Done." });
    });
    const executor = {
      execute(name: string) {
        if (name === "brain_save") {
          return { operation: "created", brainScope: "private", path: "wiki/lists/to-do.md" };
        }
        if (name === "add_media") {
          return { kind: "movie", added: true, title: "Arrival", tmdbId: 329865 };
        }
        if (name === "manage_sonarr_episodes") {
          return { action: "search_episodes", performed: true, queued: true, commandId: 8675309 };
        }
        if (name === "brain_remove_list_item") {
          return { removed: true, brainScope: "private" };
        }
        return { id: 42, text: "Call the dentist" };
      },
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Save that and remind me" }],
      context: context({ isOwner: true, surface: "dm", channelId: "D123" }),
    });
    expect(result.writeReceipts).toEqual([
      "Saved to Matt-Private",
      "Reminder created",
      "Removed from Matt-Private",
      "Added to Radarr",
      "Sonarr episode search queued",
    ]);
    expect(JSON.stringify(result.writeReceipts)).not.toContain("wiki/");
    expect(JSON.stringify(result.writeReceipts)).not.toContain("42");
    expect(JSON.stringify(result.writeReceipts)).not.toContain("329865");
    expect(JSON.stringify(result.writeReceipts)).not.toContain("8675309");
  });

  test("distinguishes scheduled and immediately active directive receipts", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "directive-scheduled",
                type: "function",
                function: { name: "create_temporary_directive", arguments: "{}" },
              },
            ],
          })
        : completion({ content: "Scheduled." });
    });
    const executor = {
      execute: () => ({ id: 9, state: "scheduled", startsAt: Date.now() + 60_000 }),
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Start concise replies in a minute" }],
      context: context(),
    });
    expect(result.writeReceipts).toEqual(["Temporary directive scheduled"]);
  });

  test("coalesces create-cancel-recreate schedules into one corrected receipt", async () => {
    const db = testDatabase();
    try {
      const actions = new ActionJournalRepository(db);
      let calls = 0;
      const wrongCommand = "SECRET wrong-time scheduled command";
      const correctedCommand = "SECRET corrected-time scheduled command";
      const client = new OpenRouterClient(config, async () => {
        calls += 1;
        return calls === 1
          ? completion({
              content: null,
              tool_calls: [
                {
                  id: "create-wrong",
                  type: "function",
                  function: {
                    name: "create_scheduled_task",
                    arguments: JSON.stringify({ command: wrongCommand }),
                  },
                },
                {
                  id: "cancel-wrong",
                  type: "function",
                  function: { name: "cancel_reminder", arguments: '{"id":11}' },
                },
                {
                  id: "create-correct",
                  type: "function",
                  function: {
                    name: "create_scheduled_task",
                    arguments: JSON.stringify({ command: correctedCommand }),
                  },
                },
              ],
            })
          : completion({ content: "I corrected the scheduled time." });
      });
      let creations = 0;
      const executor = {
        execute(name: string) {
          if (name === "cancel_reminder") return { cancelled: true, kind: "agent_task" };
          creations += 1;
          return {
            id: creations === 1 ? 11 : 12,
            kind: "agent_task",
            next_run_at: creations === 1 ? 1000 : 2000,
          };
        },
      } as unknown as ToolExecutor;
      const current = context();
      const result = await new Agent(client, executor, undefined, undefined, actions).run({
        messages: [{ role: "user", content: "Schedule this at five" }],
        context: current,
      });

      expect(result.writeReceipts).toEqual(["Scheduled task corrected"]);
      expect(result.tools).toEqual(["create_scheduled_task", "cancel_reminder"]);
      expect(actions.list({ context: current, limit: 10 })).toMatchObject({
        actions: [
          { summary: "Created a scheduled task", scheduledFor: "1970-01-01T00:00:02.000Z" },
          { summary: "Cancelled a scheduled task" },
          { summary: "Created a scheduled task", scheduledFor: "1970-01-01T00:00:01.000Z" },
        ],
      });
      const stored = JSON.stringify(db.raw.query("SELECT * FROM action_journal").all());
      expect(stored).not.toContain(wrongCommand);
      expect(stored).not.toContain(correctedCommand);
    } finally {
      db.close();
    }
  });

  test("awaits network-backed compiled tools before the next model step", async () => {
    let calls = 0;
    let secondRequest = "";
    const client = new OpenRouterClient(config, async (_input, init) => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "fetch-page",
              type: "function",
              function: {
                name: "fetch_url",
                arguments: '{"url":"https://example.com"}',
              },
            },
          ],
        });
      }
      secondRequest = String(init?.body);
      return completion({ content: "Page read." });
    });
    const executor = {
      execute: async () => ({ untrusted: true, title: "Example", text: "resolved page text" }),
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Read https://example.com" }],
      context: context(),
    });
    expect(result.text).toBe("Page read.");
    expect(secondRequest).toContain("resolved page text");
    expect(secondRequest).not.toContain("[object Promise]");
  });

  test("does not emit a receipt for a rejected deletion", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "memory-delete",
                type: "function",
                function: { name: "delete_memory", arguments: JSON.stringify({ id: 99 }) },
              },
            ],
          })
        : completion({ content: "That memory was not available." });
    });
    const executor = { execute: () => ({ deleted: false }) } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Delete memory 99" }],
      context: context(),
    });
    expect(result.writeReceipts).toEqual([]);
  });

  test("does not emit a receipt when Sonarr requires shared-file confirmation", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "sonarr-delete",
                type: "function",
                function: {
                  name: "manage_sonarr_episodes",
                  arguments: JSON.stringify({
                    action: "delete_episode_files",
                    series_title: "Doctor Who",
                    episodes: [{ season_number: 4, episode_number: 9 }],
                  }),
                },
              },
            ],
          })
        : completion({ content: "That file also contains S04E10, so I did not delete it." });
    });
    const executor = {
      execute: () => ({
        action: "delete_episode_files",
        performed: false,
        requiresSharedFileConfirmation: true,
      }),
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Delete Doctor Who S04E09" }],
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm" }),
    });
    expect(result.writeReceipts).toEqual([]);
  });

  test("replaces a false model success claim after a failed media addition", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "media-add",
                type: "function",
                function: {
                  name: "add_media",
                  arguments: JSON.stringify({ kind: "series", title: "Widow's Bay", year: 2026 }),
                },
              },
            ],
          })
        : completion({ content: "The request was sent and Sonarr probably added it." });
    });
    const executor = {
      execute: () => {
        throw new Error("sonarr returned more than FigAi's 2 MB response limit.");
      },
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Add Widow's Bay (2026)" }],
      context: context({ isOwner: true }),
    });
    expect(result.text).toBe(
      "The addition was not confirmed: sonarr returned more than FigAi's 2 MB response limit. I’m treating it as not added.",
    );
    expect(result.text).not.toContain("request was sent");
    expect(result.writeReceipts).toEqual([]);
  });

  test("replaces a false model success claim after a failed Sonarr mutation", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return calls === 1
        ? completion({
            content: null,
            tool_calls: [
              {
                id: "sonarr-delete",
                type: "function",
                function: {
                  name: "manage_sonarr_episodes",
                  arguments: JSON.stringify({
                    action: "delete_episode_files",
                    series_title: "Severance",
                    episodes: [{ season_number: 2, episode_number: 4 }],
                  }),
                },
              },
            ],
          })
        : completion({ content: "Deleted it." });
    });
    const executor = {
      execute: () => {
        throw new Error("sonarr returned HTTP 500.");
      },
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Delete Severance S02E04" }],
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm" }),
    });
    expect(result.text).toBe(
      "The Sonarr operation was not confirmed: sonarr returned HTTP 500. I’m treating it as not performed.",
    );
    expect(result.writeReceipts).toEqual([]);
  });

  test("carries successful write receipts through a later provider failure", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "memory-save",
              type: "function",
              function: { name: "save_memory", arguments: "{}" },
            },
          ],
        });
      }
      return new Response("provider failed", { status: 400 });
    });
    const executor = { execute: () => ({ id: 7 }) } as unknown as ToolExecutor;
    try {
      await new Agent(client, executor).run({
        messages: [{ role: "user", content: "Remember this" }],
        context: context({ surface: "dm", channelId: "D123" }),
      });
      throw new Error("Expected the agent run to fail.");
    } catch (error) {
      expect(error).toBeInstanceOf(AgentRunError);
      expect((error as AgentRunError).writeReceipts).toEqual(["Private memory saved"]);
    }
  });

  test("loads a relevant instruction skill through the normal guarded tool loop", async () => {
    let calls = 0;
    let followupMessages: unknown;
    const client = new OpenRouterClient(config, async (_url, init) => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "skill-1",
              type: "function",
              function: { name: "load_skill", arguments: '{"id":7}' },
            },
          ],
        });
      }
      followupMessages = (JSON.parse(String(init?.body)) as { messages: unknown }).messages;
      return completion({ content: "Here are the release notes." });
    });
    const executor = {
      execute(name: string, rawArguments: string) {
        expect(name).toBe("load_skill");
        expect(rawArguments).toBe('{"id":7}');
        return {
          untrusted: true,
          id: 7,
          name: "Release notes",
          instructions: "Group changes by customer impact.",
        };
      },
    } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Write release notes for these changes" }],
      context: context(),
    });
    expect(JSON.stringify(followupMessages)).toContain("Group changes by customer impact");
    expect(result.tools).toContain("load_skill");
  });

  test("loads Brain Librarian before organizing an explicit Brain capture", async () => {
    let calls = 0;
    const executed: Array<{ name: string; arguments: string }> = [];
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "brain-librarian",
              type: "function",
              function: { name: "load_skill", arguments: '{"id":1}' },
            },
          ],
        });
      }
      if (calls === 2) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "canonical-search",
              type: "function",
              function: {
                name: "brain_search",
                arguments: JSON.stringify({ query: "To Do Home", limit: 5 }),
              },
            },
          ],
        });
      }
      if (calls === 3) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "organized-save",
              type: "function",
              function: {
                name: "brain_save",
                arguments: JSON.stringify({
                  destination_kind: "list",
                  destination_title: "To Do",
                  text: "Contact the sump-pump company",
                  entry_kind: "task",
                  section: "Home",
                  topics: ["Home"],
                }),
              },
            },
          ],
        });
      }
      return completion({ content: "Added it to your home tasks." });
    });
    const executor = {
      execute(name: string, rawArguments: string) {
        executed.push({ name, arguments: rawArguments });
        if (name === "load_skill") {
          return {
            untrusted: true,
            id: 1,
            name: "Brain Librarian",
            instructions: "Reuse canonical notes and put home tasks in To Do under Home.",
          };
        }
        if (name === "brain_search") return { results: [{ title: "To Do" }] };
        return { saved: true, operation: "updated", brainLabel: "Matt-Private" };
      },
    } as unknown as ToolExecutor;

    const result = await new Agent(client, executor).run({
      messages: [
        { role: "user", content: "Remember that I need to contact the sump-pump company." },
      ],
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" }),
    });

    expect(executed.map((call) => call.name)).toEqual(["load_skill", "brain_search", "brain_save"]);
    expect(JSON.parse(executed[2]?.arguments ?? "{}")).toMatchObject({
      destination_kind: "list",
      destination_title: "To Do",
      entry_kind: "task",
      section: "Home",
    });
    expect(result.tools).toEqual(["load_skill", "brain_search", "brain_save"]);
  });

  test("removes an exact Brain list item after a recorded completion follow-up", async () => {
    let calls = 0;
    const executed: Array<{ name: string; arguments: string }> = [];
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "find-to-do",
              type: "function",
              function: {
                name: "brain_search",
                arguments: JSON.stringify({ query: "To Do AppleCare", limit: 5 }),
              },
            },
          ],
        });
      }
      if (calls === 2) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "read-to-do",
              type: "function",
              function: { name: "brain_read", arguments: '{"path":"brain-ref:todo"}' },
            },
          ],
        });
      }
      if (calls === 3) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "remove-applecare",
              type: "function",
              function: {
                name: "brain_remove_list_item",
                arguments: JSON.stringify({
                  destination_title: "To Do",
                  text: "Look into AppleCare",
                }),
              },
            },
          ],
        });
      }
      return completion({ content: "AppleCare is confirmed done and removed from To Do." });
    });
    const executor = {
      execute(name: string, rawArguments: string) {
        executed.push({ name, arguments: rawArguments });
        if (name === "brain_search") {
          return { results: [{ path: "brain-ref:todo", title: "To Do" }] };
        }
        if (name === "brain_read") return { content: "- Look into AppleCare" };
        return { removed: true, brainScope: "private" };
      },
    } as unknown as ToolExecutor;

    const result = await new Agent(client, executor).run({
      messages: [
        {
          role: "system",
          content:
            "A just-satisfied directive records this one-time follow-up: remove AppleCare from To Do after confirmed completion.",
        },
        { role: "user", content: "[Matt] I finished looking into AppleCare." },
      ],
      context: context({ requesterId: "UOWNER", isOwner: true, surface: "dm", channelId: "D1" }),
    });

    expect(executed.map((call) => call.name)).toEqual([
      "brain_search",
      "brain_read",
      "brain_remove_list_item",
    ]);
    expect(result.writeReceipts).toEqual(["Removed from Matt-Private"]);
    expect(result.text).toContain("removed from To Do");
  });

  test("retrieves current and target Brain notes for an ordinary owner-DM gear question", async () => {
    let calls = 0;
    const executed: Array<{ name: string; arguments: string }> = [];
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "brain-search",
              type: "function",
              function: {
                name: "brain_search",
                arguments: JSON.stringify({ query: "Pindruid Mags", limit: 5 }),
              },
            },
          ],
        });
      }
      if (calls === 2) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "brain-current",
              type: "function",
              function: {
                name: "brain_read",
                arguments: JSON.stringify({ path: "wiki/projects/pindruid-current-gear.md" }),
              },
            },
          ],
        });
      }
      if (calls === 3) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "brain-targets",
              type: "function",
              function: {
                name: "brain_read",
                arguments: JSON.stringify({ path: "wiki/projects/pindruid-upgrade-targets.md" }),
              },
            },
          ],
        });
      }
      return completion({
        content:
          "You still need **Eye of Magtheridon**. [[wiki/projects/pindruid-upgrade-targets]]",
      });
    });
    const executor = {
      execute(name: string, rawArguments: string) {
        executed.push({ name, arguments: rawArguments });
        if (name === "brain_search") {
          return {
            results: [
              { path: "wiki/projects/pindruid-current-gear.md" },
              { path: "wiki/projects/pindruid-upgrade-targets.md" },
            ],
          };
        }
        const path = (JSON.parse(rawArguments) as { path: string }).path;
        return path.includes("current-gear")
          ? { path, content: "Band of Crimson Fury is equipped." }
          : { path, content: "Eye of Magtheridon: still needed. Band: already covered." };
      },
    } as unknown as ToolExecutor;

    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "what does pindruid need from mags" }],
      context: context({
        requesterId: "UOWNER",
        isOwner: true,
        surface: "dm",
        channelId: "D123",
      }),
    });

    expect(executed.map((call) => call.name)).toEqual(["brain_search", "brain_read", "brain_read"]);
    expect(result.text).toContain("Eye of Magtheridon");
    expect(result.text).not.toContain("Band of Crimson Fury");
    expect(result.text).toContain("[[wiki/projects/pindruid-upgrade-targets]]");
    expect(result.tools).toEqual(["brain_search", "brain_read"]);
    expect(result.tools).not.toContain("load_skill");
  });

  test("does not append web citations to an ordinary factual answer", async () => {
    const client = new OpenRouterClient(config, async () =>
      completion({
        content: "Yes, rain starts around six.",
        annotations: [
          {
            type: "url_citation",
            url_citation: { url: "https://example.com/weather", title: "Forecast" },
          },
        ],
      }),
    );
    const executor = { execute: () => undefined } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Is it going to rain tonight?" }],
      context: context(),
    });
    expect(result.text).toBe("Yes, rain starts around six.");
  });

  test("sends one model-requested progress message and continues to the final answer", async () => {
    let calls = 0;
    const progress: string[] = [];
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      if (calls === 1) {
        return completion({
          content: null,
          tool_calls: [
            {
              id: "progress-1",
              type: "function",
              function: {
                name: "send_progress",
                arguments: JSON.stringify({ message: "I’m comparing the raid histories now." }),
              },
            },
          ],
        });
      }
      return completion({ content: "Wrath wins." });
    });
    const executor = { execute: () => undefined } as unknown as ToolExecutor;
    const result = await new Agent(client, executor).run({
      messages: [{ role: "user", content: "Research every WoW raid era" }],
      context: context(),
      onProgress: async (message) => {
        progress.push(message);
      },
    });
    expect(progress).toEqual(["I’m comparing the raid histories now."]);
    expect(result.text).toBe("Wrath wins.");
    expect(result.tools).toContain("send_progress");
  });

  test("stops after eight model tool steps", async () => {
    let calls = 0;
    const client = new OpenRouterClient(config, async () => {
      calls += 1;
      return completion({
        content: null,
        tool_calls: [
          {
            id: String(calls),
            type: "function",
            function: { name: "list_memories", arguments: "{}" },
          },
        ],
      });
    });
    const executor = { execute: () => [] } as unknown as ToolExecutor;
    await expect(
      new Agent(client, executor).run({
        messages: [{ role: "user", content: "loop" }],
        context: context(),
      }),
    ).rejects.toThrow("eight-step");
    expect(calls).toBe(8);
  });
});
