import { expect, test } from "bun:test";
import { redact } from "../src/logger.ts";

test("secret redaction covers keys and known token prefixes", () => {
  expect(
    redact({ token: "xoxb-secret", nested: { value: "use sk-or-v1-abc123", safe: "hello" } }),
  ).toEqual({ token: "[REDACTED]", nested: { value: "use [REDACTED]", safe: "hello" } });
  expect(
    redact({
      profile: { display_name: "Private", image_512: "https://avatars.slack-edge.com/a.png" },
      avatarUrl: "https://avatars.slack-edge.com/a.png",
      image_url: { url: "data:image/png;base64,secret-bytes" },
    }),
  ).toEqual({ profile: "[REDACTED]", avatarUrl: "[REDACTED]", image_url: "[REDACTED]" });
  expect(
    redact({
      sonarrApiKey: "sonarr-secret",
      sabnzbd_apikey: "sab-secret",
      authorization: "Bearer media-secret",
    }),
  ).toEqual({
    sonarrApiKey: "[REDACTED]",
    sabnzbd_apikey: "[REDACTED]",
    authorization: "[REDACTED]",
  });
});
