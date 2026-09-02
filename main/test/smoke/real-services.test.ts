import { expect, test } from "bun:test";

const enabled = process.env.RUN_SMOKE_TESTS === "1";
const smoke = enabled ? test : test.skip;

smoke("real Slack and OpenRouter credentials are accepted", async () => {
  const botToken = process.env.SLACK_BOT_TOKEN;
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!botToken || !apiKey)
    throw new Error("Set SLACK_BOT_TOKEN and OPENROUTER_API_KEY for smoke tests.");
  const [slack, openRouter] = await Promise.all([
    fetch("https://slack.com/api/auth.test", { headers: { Authorization: `Bearer ${botToken}` } }),
    fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: process.env.PRIMARY_MODEL ?? "openai/gpt-5.6-luna",
        messages: [{ role: "user", content: "Reply with exactly: smoke-ok" }],
        max_tokens: 10,
      }),
    }),
  ]);
  expect((await slack.json()) as { ok: boolean }).toHaveProperty("ok", true);
  expect(openRouter.ok).toBeTrue();
});
