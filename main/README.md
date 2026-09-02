# FigAi

FigAi is a single-workspace Slack assistant that runs locally on macOS or Linux. It uses Bun, TypeScript, Slack Bolt Socket Mode, SQLite, OpenRouter, and an OS-native user service (`launchd` on macOS or `systemd` on Linux). By default it opens no inbound network listener; the only opt-in exception is an unauthenticated web chat UI reachable from the LAN (see "Local web UI" below). It has no repository, email, or workspace-search tool. Its only shell-equivalent capability is explicitly configured, owner-only, confirm-before-execute SSH access to named machines (see "Remote SSH commands" below); it has no other shell tool. Its autonomous work is limited to reminders, agent tasks, and durable workflows explicitly requested by an authorized user.

Ask FigAi how much the last answer cost, or for the current thread's usage, to see provider-reported model, token, latency, tool, and cost statistics.

## Slack app setup

1. In [Slack's app management UI](https://api.slack.com/apps), create an app **from a manifest** and paste `slack-manifest.yaml`.
2. Under **Basic Information → App-Level Tokens**, create a token with `connections:write`. This is the `xapp-…` token.
3. Install the app to exactly one workspace and copy its `xoxb-…` bot token.
4. Invite FigAi to each approved public or private channel. Use channel IDs—not names—in configuration. The app intentionally ignores every other channel.
5. If you change event subscriptions or scopes, reinstall the Slack app.

The manifest subscribes to `app_mention`, `message.im`, `message.channels`, and `message.groups`, enables `/figai`, and contains no credentials. Ordinary channel replies require a fresh mention; unmentioned channel messages are inspected only while that sender has a workflow waiting for evidence, and otherwise return before authorization or storage. Authorized one-to-one DMs respond to every message. Group DMs, external/Slack Connect users, bots, edits, and unsupported message subtypes are ignored. The existing `users:read` scope is sufficient for the scoped profile and avatar lookup; no workspace-search scope is required.

The connection follows Slack's [Socket Mode model](https://docs.slack.dev/tools/bolt-js/concepts/socket-mode/), so no request URL or public endpoint is required.

## Local configuration

On macOS:

```sh
mkdir -p "$HOME/Library/Application Support/FigAi"
cp .env.example "$HOME/Library/Application Support/FigAi/.env"
chmod 600 "$HOME/Library/Application Support/FigAi/.env"
```

On Linux:

```sh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/figai"
cp .env.example "${XDG_CONFIG_HOME:-$HOME/.config}/figai/.env"
chmod 600 "${XDG_CONFIG_HOME:-$HOME/.config}/figai/.env"
```

Edit that `.env` and set:

- `SLACK_BOT_TOKEN`: workspace bot token (`xoxb-…`).
- `SLACK_APP_TOKEN`: Socket Mode app token (`xapp-…`).
- `OPENROUTER_API_KEY`: OpenRouter API key.
- `OWNER_USER_ID`: the owner's Slack member ID.
- `ALLOWED_CHANNEL_IDS`: comma-separated approved channel IDs.
- `DIRECTIVE_POLICY_MODEL` (optional): model used to compile and enforce temporary directives; defaults to `openai/gpt-5.6-luna`.
- `OBSIDIAN_VAULT_PATH` (optional): absolute path to the owner's structured Obsidian Brain vault.
- `SONARR_URL` and `SONARR_API_KEY` (optional pair): exact local Sonarr origin and API key.
- `RADARR_URL` and `RADARR_API_KEY` (optional pair): exact local Radarr origin and API key.
- `SABNZBD_URL` and `SABNZBD_API_KEY` (optional pair): exact local SABnzbd origin and full API key.
- `SSH_HOSTS_JSON` (optional): a single-line JSON object of owner-only SSH targets, keyed by lowercase alias. See "Remote SSH commands" below.
- `FIGAI_DATA_DIR` (optional): absolute application-data override. Linux otherwise uses `${XDG_DATA_HOME:-$HOME/.local/share}/figai`.
- `WEB_UI_PORT` (optional): serves a local chat UI on that port. See "Local web UI" below.

The text model defaults are `openai/gpt-5.6-luna` and `google/gemini-3.7-flash`; both can be overridden. Luna is the cost-efficient primary and default directive-policy model, while Gemini is used when a transient primary-provider failure requires one fallback attempt. Directive policy remains a separate constrained model call and intentionally does not use the cheaper loading-status model. Image generation defaults to `google/gemini-3.1-flash-lite-image` and can be overridden with `IMAGE_GENERATION_MODEL`. Web research uses Exa with at most eight results, avoiding a separate high-cost model pass for search. Secrets never appear in the Slack manifest, LaunchAgent, or systemd unit.

## Development

```sh
bun install --frozen-lockfile
bun run check
bun test
bun run dev
```

Normal tests are credential-free. The opt-in smoke test is separate:

```sh
RUN_SMOKE_TESTS=1 bun test test/smoke
```

## Install, update, and remove

```sh
./scripts/install.sh
./scripts/status.sh
./scripts/update.sh
./scripts/uninstall.sh
```

`install.sh` detects the current OS and architecture, runs checks and tests, compiles a native Bun executable, installs it into the platform's application-data directory, and starts the appropriate user service.

- macOS installs under `~/Library/Application Support/FigAi/`, loads `~/Library/LaunchAgents/com.matgra.figai.plist`, and writes logs to `~/Library/Logs/FigAi/`.
- Linux installs under `${XDG_DATA_HOME:-$HOME/.local/share}/figai/`, loads `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/figai.service`, and writes logs to the user journal. Inspect them with `journalctl --user-unit figai.service`.

Linux requires a user `systemd` session. The service normally runs while that user is logged in. An administrator may enable lingering with `loginctl enable-linger <user>` when the bot must start at boot and remain active after logout. Brain map exports additionally require `rsvg-convert` or ImageMagick; on Debian/Ubuntu, install `librsvg2-bin`.

Native and cross-platform binaries can also be built directly:

```sh
bun run build
bun run build:linux:x64
bun run build:linux:arm64
bun run build:mac:arm64
```

GitHub Actions runs the complete test and native build on macOS and Linux and publishes macOS, Linux x64, and Linux ARM64 workflow artifacts.

Uninstall preserves secrets, SQLite data, backups, and logs. macOS moves the service file and executable to Trash; Linux moves them into a timestamped recovery directory under the application-data directory.

## Behavior and controls

FigAi reads only the invoking Slack thread: its root and newest messages, capped at 80 messages or 60,000 characters. Explicit personal memories remain DM-only; channel memories remain in that channel. Typed response preferences—language, verbosity, tone, format, and units—belong to one Slack user and follow that user across authorized DMs and channels. Current-message instructions override those defaults, and preferences never affect permissions or tool authorization. Attachments support JPEG, PNG, WebP, GIF, PDF, Markdown, and plain text, with limits of 10 MB per media/PDF file, 1 MB per text file, four files per turn, and 20 MB total. Text must be strict UTF-8 and is capped at 200,000 characters. Temporary files are mode `0600` and removed after every turn.

Temporary directives provide condition- or time-bound behavior without turning it into a permanent preference. Every directive is keyed to its creator and follows that user across all authorized DMs, channels, and threads; it may activate at a stored future time, expire, or be cancelled. Future activation is enforced by SQLite state rather than prose inside an already-active instruction.

At creation time, the policy model compiles the request into a versioned, validated policy describing delivery behavior, tool behavior, and concise requirements. Compilation uses strict structured output; if it is unavailable or invalid, no directive is saved. Legacy active directives are upgraded lazily. A direct delivery-suppression policy skips the main model, and active restrictions are checked again before local tool execution and immediately before Slack delivery. Other behavioral constraints receive semantic compliance review and bounded regeneration attempts. Policy failures fail closed: FigAi suppresses a potentially non-compliant action instead of treating a verifier error as permission.

The same policy model judges whether the latest message credibly satisfies a release condition using ordinary conversational meaning rather than exact string matching. It may also grant a one-turn bypass for an explicit request to inspect, cancel, replace, or override the directive, allowing the normal directive-management tools to run without silently deleting the rule. Ordinary chatter or coaxing does not receive that bypass. Every sanitized ingress and compliance verdict is recorded in the scoped action journal, without saving directive text or model reasoning. When a condition is satisfied, the normal turn receives the just-completed directive as context so it can perform an explicitly recorded one-time follow-up without continuing the old restriction. There is no separate focus-lock mechanism. Active directives may restrict or customize behavior but cannot grant permissions, authorize tools, or weaken policy. Scheduled notifications continue independently.

While a turn runs, Slack shows a five-stage, request-specific loading progression in chronological order. The stages remain mildly snarky, stay within Slack's 50-character status limit, and fall back to a fixed ordered sequence if contextual generation is unavailable.

Successful persistent changes include a compact final receipt such as `✓ Saved to Matt-Private`, `✓ Memory deleted`, `✓ Reminder created`, or `✓ Scheduled task created`. Receipts are derived from successful tool results rather than model prose, never appear for reads or rejected changes, and omit internal IDs, paths, and raw arguments. When FigAi creates a schedule, cancels that exact record, and recreates the same kind of schedule in one turn to correct it, the three implementation receipts collapse to `✓ Reminder corrected` or `✓ Scheduled task corrected`.

When asked what it previously did, whether it used a tool, or why an earlier action occurred, FigAi can inspect a read-only action journal for the exact current Slack thread and requester. The journal stores only an allowlisted tool name, success/no-change/failure state, a generic summary, timing, and the scheduled time when relevant. Raw tool arguments, commands, profile payloads, avatar data and URLs, tool results, error text, Brain content, memory content, paths, and action target IDs are never stored or returned to the model. Action metadata expires after 30 days and cannot be searched across users, channels, threads, or workspaces.

Web research uses OpenRouter's [`openrouter:web_search` server tool](https://openrouter.ai/docs/guides/features/server-tools/web-search), private image/PDF inputs use its [base64 multimodal formats](https://openrouter.ai/docs/guides/overview/multimodal/overview), and generated images use OpenRouter's dedicated Image API before being uploaded directly to the Slack thread.

When a user supplies a specific public URL, the compiled `fetch_url` reader can download up to 2 MB of UTF-8 HTML, Markdown, plain text, JSON, CSV, or XML and return at most 60,000 characters of readable text. It does not run JavaScript, maintain cookies, submit forms, click, download binary files, or expose a browser. Requests permit only standard HTTP/HTTPS ports, pin a validated public DNS address, revalidate every redirect, and reject localhost, private, link-local, reserved, and unresolved addresses. Page contents and links remain untrusted data.

### Local media services

When configured, authorized users can inspect Sonarr, Radarr, and SABnzbd in any DM or approved channel where FigAi is allowed to respond. The compiled `inspect_media_service` tool provides read-only status, library, missing-item, queue, history, quality-profile, root-folder, naming, download-client, category, and allowlisted configuration views where the selected service supports them. It never accepts a model-supplied host or API path.

Only the configured Slack owner may use `add_media`, from either the owner's DM or an approved channel, and only after explicitly requesting a specific movie or series. FigAi resolves the title through the appropriate service, refuses ambiguous matches without a year, reuses the most common existing root folder and quality profile unless the owner specifies named choices, and can ask Sonarr or Radarr to search immediately. Duplicate titles produce no change, and successful additions receive a trusted `Added to Sonarr` or `Added to Radarr` receipt footer.

The owner may use `manage_sonarr_episodes` only in a one-to-one DM. It can queue an `EpisodeSearch` for up to 100 explicit season/episode selectors, queue a `SeasonSearch` for one named season, or permanently delete downloaded files for up to 20 explicit selectors. It cannot run a whole-series or whole-library search, delete a series, alter monitoring/settings, mutate Radarr or SABnzbd, or accept an arbitrary API path. Titles are resolved through Sonarr and ambiguous remakes require a year. A queued search means Sonarr accepted the search command, not that a release was found or downloaded.

FigAi makes no deletion request when Sonarr reports that any selected episode lacks a file. Multi-episode files are detected before deletion: FigAi reports every additional episode sharing the file and makes no change until the owner explicitly confirms those effects in a later DM message. Successful searches and deletions receive trusted receipt footers, while disambiguation, missing files, and shared-file warnings receive none.

The generic URL reader remains unable to access private networks. Local access exists only through the three exact origins supplied in the protected mode-`0600` `.env`. Sonarr and Radarr API keys are sent in the `X-Api-Key` header; SABnzbd's required API-key query parameter is constructed internally and never returned. Responses are capped, marked untrusted, and recursively redact passwords, usernames, tokens, cookies, API keys, credentials, certificates, and secret-valued fields before reaching the model. No service response or credential is stored in SQLite.

These integrations do not add a container, inbound listener, plugin runtime, shell, arbitrary localhost access, or browser automation. Mutations are limited to owner-gated media additions plus the bounded Sonarr episode operations above. Sonarr/Radarr/SABnzbd API keys are broadly privileged, so keep FigAi's `.env` private.

### Remote SSH commands

When `SSH_HOSTS_JSON` is configured, the owner can ask FigAi to run a command on one of the named machines. This is the one deliberate shell-equivalent capability in the app, so it is deliberately narrow:

- **Owner-only.** No other authorized user can propose or confirm an SSH command.
- **Fixed host allowlist.** FigAi can only target a host alias predefined in `SSH_HOSTS_JSON` (e.g. `nas`, `homelab`). The model is never given a raw hostname, IP, or username to connect to, and cannot invent an alias.
- **Two-step confirmation.** Asking FigAi to run a command only drafts it with `propose_ssh_command`, which previews the exact host alias and command text. Nothing runs until the owner explicitly approves that preview in a later Slack message and FigAi calls `resolve_ssh_command`; confirming in the same turn the draft was shown is rejected. A pending draft expires after 24 hours.
- **Arbitrary command on the target host.** Once confirmed, the full command string runs on the remote host's own shell over `ssh` in batch mode (no password prompts, no interactive host-key acceptance—`StrictHostKeyChecking=yes`, so a new host must already be in `known_hosts`). There is no allowlist of remote commands; treat every configured host as fully exposed to the owner's Slack account.
- **Bounded, untrusted output.** stdout/stderr are capped, returned to the model as untrusted data, and never presented as instructions. A 30-second timeout kills a hung command.
- **Auditable, not silent.** Every confirmed command's host alias, command text, requester, exit code, and timeout state are written to an immutable audit table (`ssh_command_audit`)—command text is retained for accountability, but raw stdout/stderr are not persisted.

Configure it with a single-line JSON object keyed by lowercase alias:

```sh
SSH_HOSTS_JSON={"nas":{"host":"192.168.1.10","user":"matt","port":22,"keyPath":"/Users/matt/.ssh/id_ed25519_nas"}}
```

`port` defaults to `22`; omitting `keyPath` falls back to `ssh`'s normal identity resolution (an running `ssh-agent` or its default key files). Because this grants shell access to whatever the target user account can do, only add hosts and keys you are comfortable handing to FigAi's owner, and keep the target account's own privileges as narrow as the task requires.

### Local web UI

When `WEB_UI_PORT` is configured, FigAi also serves a minimal browser chat UI on that port alongside Slack, so you can talk to it directly without going through Slack. It's reachable at `http://localhost:<port>` on this machine and at `http://<this machine's LAN IP>:<port>` from any other device on your network:

- **No authentication.** The server listens on all interfaces with no login of any kind—anyone who can reach the port, including anyone else on your LAN or WiFi, gets the page and full owner-level tool access. Only run this on a network you trust, and do not forward the port to the internet or put it behind a public reverse proxy without adding your own authentication first.
- **Same owner, same tools.** The web session always runs as the configured owner in a single persistent conversation, so it has full owner-level tool access—Brain, reminders, skills, media, SSH commands with the same propose/confirm flow, everything Slack has.
- **Change models on the fly.** The header has a model field; typing a `provider/model` id and submitting validates it against OpenRouter's catalog and switches immediately, same as `/figai model`. A reset button restores `PRIMARY_MODEL` from `.env`.
- **Separate conversation history.** The web chat keeps its own in-memory thread (cleared on restart) and its own SSH/skill confirmation state, isolated from any Slack thread; personal memories, preferences, and directives are still shared with the owner's Slack DM since they belong to the same user.
- **Scope of this first pass:** temporary-directive release detection and durable-workflow event matching, which are driven by inbound Slack messages, do not run on the web surface yet; everything else works the same.

FigAi resolves the safe display name of each internal human participating in the loaded Slack thread and labels that participant's messages for the model. This uses the existing `users:read` scope and does not expose Slack IDs to the model or persist participant profiles. On an explicit request to view, describe, compare, or use a Slack profile/avatar, FigAi can inspect the requester, owner, or an internal user participating in the current thread, including by a uniquely resolved participant name. It returns only selected profile fields and, when visual inspection is requested, downloads one validated image (10 MB maximum) into mode-`0600` temporary storage, forwards it to the model as base64, and deletes it after the turn. Profiles are never enumerated, injected automatically, or stored in SQLite or logs.

### Instruction skills

The owner can teach FigAi reusable, global procedures through ordinary messages. A skill has a name, a short matching description, and Markdown instructions. FigAi places only enabled skill metadata in normal prompts and loads the complete instructions when a description clearly matches the current request.

Creating or revising a skill always produces an exact preview first. The owner must confirm that preview in a later message in the same Slack thread; pending previews expire after 24 hours. The owner can also ask FigAi to list, inspect, enable, disable, or delete skills. Up to 25 skills may be enabled at once.

Skills are lower-priority instruction text, not executable plugins. They cannot create tools, run shell commands, access repositories or files, grant permissions, override FigAi's system policy, or initiate their own changes. Do not put secrets in skills. Skill revisions and state changes are audited in SQLite and included in the normal daily backups.

FigAi seeds one enabled, global `Brain Librarian` skill. It chooses durable destinations, canonical notes, entry types, sections, and categories for explicit Brain captures across owner, private-user, and channel Brains. The owner can revise it through the normal preview-and-later-confirmation workflow, or disable or delete it. It improves new writes and can recommend cleanup for existing notes, but it cannot move, rename, merge, or delete vault files.

### Obsidian Brain

When `OBSIDIAN_VAULT_PATH` is configured, FigAi provides isolated Brain scopes:

- The configured Obsidian vault remains `Matt-Private`, the owner's DM write target.
- Every other authorized DM user receives a separate private Brain that only that user can access.
- Every approved Slack channel receives a separate shared Brain writable only from that channel and readable there or through the owner's federated DM view.

Read-only Brain retrieval can happen whenever a request depends on saved context; writes still require an explicit request. In the owner's DM, list, search, and read federate `Matt-Private` with every existing approved channel Brain. Writes from that DM still go only to `Matt-Private`. A channel can read and write only its own shared Brain, while another user's DM remains private to that user. The owner can never inspect another user's private Brain, and knowledge is never copied automatically between Brains.

Federated results carry a natural Brain label such as `Matt-Private` or `<channel>-Matt-Public`. Reads use short-lived, turn-bound opaque handles so identical note names resolve to the correct Brain without exposing channel IDs, hashed directory names, or vault paths. Conflicting private and public facts remain separate and must be reported with provenance rather than silently merged. Removed or unapproved channel Brains remain preserved on disk but disappear from the owner's federated DM view.

An explicit request to show, render, or export the Brain map produces a deterministic PNG from real wiki notes and their Obsidian wikilinks. In a channel, the image contains only that channel's Brain. In the owner's DM, `Matt-Private` and all existing approved channel Brains appear as separate labeled panels; other users' private Brains are never included. Rendering uses macOS `sips`, Linux `rsvg-convert`, or Linux ImageMagick inside a mode-`0700` temporary directory with mode-`0600` inputs and output, enforces the 10 MB upload limit, and deletes all temporary files on success or failure. It does not call an image model, invent relationships, persist the export, or generate maps automatically during ordinary turns.

Brain writes use an organizational upsert rather than a create-file shortcut. FigAi identifies the durable subject or collection, reuses a canonical note when one exists, and creates the appropriate person, project, area, list, topic, reference, synthesis, or inbox container when it does not. Atomic tasks and facts are added to those containers instead of becoming standalone files—for example, a home task belongs in the `To Do` list while a birthday belongs in the relevant person's note. Optional durable categories create nested folders, and uncertain material goes to Inbox rather than producing speculative structure. An explicit removal request or recorded completion follow-up may remove exactly one unambiguous item from a canonical list in the current Brain; it cannot delete notes or target another Brain. Internal vault paths and Obsidian wikilinks are never included in Slack replies.

Additional scoped vaults live under FigAi's mode-`0700` application-data directory in `brains/users/` and `brains/channels/`. Directory names are opaque hashes rather than Slack IDs. Each scope has its own evidence layer, wiki, linter, and local Git history; these vaults are separate from the SQLite backup files.

The integration follows the vault's existing evidence-first contract:

- Search is read-only and limited to Markdown under `wiki/` and `sources/`; it ranks separate query terms, frontmatter aliases, and conservative abbreviation matches. Individual reads also permit `maps/` and `Home.md`.
- Save runs the vault's fixed `scripts/vault.py capture` evidence workflow, resolves a canonical destination by title or alias, creates or updates one schema-valid wiki note, runs the vault linter, and makes a local Git commit.
- List-item removal requires an exact single match, captures immutable evidence of the requested change, runs the same linter, and makes a local Git commit. Missing or duplicate matches make no wiki change.
- Tasks, list items, facts, and prose are placed in an appropriate section. A missing section or durable container is created automatically; one task cannot create a new project.
- Writes require a clean vault. A lint failure removes or restores the generated wiki change while preserving captured source evidence for recovery.
- Model-supplied paths cannot escape the approved areas, follow symlinks elsewhere, access hidden files, or invoke arbitrary shell commands. Returned note content is treated as untrusted data.
- Scope selection comes only from trusted Slack context. The owner receives the explicit read-only federation above; no user can name or access another person's private Brain, and writes cannot target a different surface.

Obsidian does not need to be open. The application uses direct local files plus the vault's fixed Python and Git commands; it does not expose a general filesystem, shell, workspace-search, or repository tool to the model. Keep the vault itself private and backed up. The Obsidian command-line interface remains useful for manual maintenance but is not required by FigAi.

Use ordinary messages for controls, for example:

```text
What do you remember here?
Forget memory 12.
Remember that I prefer concise answers in Spanish.
Use detailed bullet points for the next hour.
Do not respond to me anywhere until I say "I finished the report."
Remind me tomorrow at 9 AM to review the report.
At 9 AM, read my current to-do list and recommend two priorities.
In ten minutes ask for a photo of my trimmer, wait 30 seconds, then nag me every five seconds until the photo is verified or I call it off.
Show my reminders.
Show my active workflows.
Search my Brain for the project architecture.
Capture this in my Brain as a project note titled FigAi Brain.
Export my Brain map.
How is FigAi doing?
What model are you using?
Switch to provider/model.
Reset to the default model.
Create a skill for writing concise release notes.
List the available instruction skills.
Disable the release-notes skill.
```

`/figai` displays FigAi's compiled capabilities. As an emergency fallback, `/figai model`, `/figai model <provider/model>`, and `/figai model reset` provide deterministic model controls; only `OWNER_USER_ID` can change or reset the model. Requested model IDs are checked against OpenRouter's current catalog before anything is saved, and private IDs with a leading `~` are resolved without silently dropping that prefix. A rejected switch leaves the working model unchanged, while an unusable primary falls back once so the chat remains recoverable. Instruction-skill management and all other operations remain message-only. Valid model overrides are stored in SQLite, take effect immediately, and survive restarts; resetting returns to `PRIMARY_MODEL` from `.env`.

FigAi uses four distinct future-action paths. A request whose known message is the entire future job becomes a cheap reminder and posts without a model call. One simple future behavior window becomes a scheduled temporary directive with database-enforced start and expiration times. A request that must reason once at a future time, inspect current Brain or memory state, research the web, call another tool, change state, or perform multiple ordered effects becomes one agent-powered scheduled task. A request that must continue across multiple timer or Slack events becomes a durable workflow. Examples include waiting for a qualifying reply or photo, timing out into repeated messages, and stopping after semantic completion or an explicit call-off. Compound future actions remain dormant until their scheduled start. In a channel, the requester must choose delivery in the current thread, as a new top-level message in that channel, or in their DM; FigAi asks rather than guessing when this is omitted. Schedules created in a DM default to that DM. Existing schedules retain their original threaded delivery. All authorized users may create reminders and tasks; durable workflows and normal owner-only tool operations are restricted to `OWNER_USER_ID`.

Durable workflows are validated graphs of message, delay, await, repeat, and completion steps—not arbitrary code or continuously running agents. Await steps can use semantic text evidence, attachment presence, or actual model inspection of a validated image. A typed completion policy can also count trusted successful FigAi actions across turns; currently, successful removals from a Brain list can satisfy workflows such as “stop after I finish two To Do items.” Event keys are hashed and deduplicated, so task text and raw Slack identifiers are not stored in workflow event records. The runtime persists the current step and completion count, sleeps until the next deadline, resumes on matching Slack or trusted tool events, and uses the model only when judgment or generation is required. Workflows expire after at most seven days, repeat no faster than every five seconds, and stop after 500 sent messages. Terminal workflows are retained for audit with a `deleted_at` timestamp instead of being physically deleted; timers and leases are cleared immediately. Workflow plans and state persist in SQLite; reply text, attachment bytes, private attachment URLs, and raw tool arguments do not.

Daily and weekly schedules save their presentation at creation time. Recurring reminders receive a task-specific title; recurring agent tasks also save formatting instructions for stable ordering, sections, change emphasis, empty-state wording, tone, and length. Every occurrence reuses that contract so recurring notifications remain recognizable without a generic scheduler label. One-time agent results are written as natural standalone notifications.

A scheduled command may explicitly require silence when a condition is met. In that case the agent records a durable successful silent outcome, advances or completes the schedule, and posts nothing to Slack; silence is not represented as an empty or failed model response.

Daily and weekly schedules preserve the user's local wall-clock time through DST. A sleeping or offline Mac may deliver one occurrence late; recurring schedules skip surplus missed occurrences. Reminders and agent tasks share the limits of 25 active and 10 recurring schedules per user. Agent tasks use the model active when they run, record provider usage, retry a transient provider failure once only before a successful write, and are never replayed after a write or interrupted run.

## Data and backups

The database is `~/Library/Application Support/FigAi/figai.sqlite` on macOS and `${XDG_DATA_HOME:-$HOME/.local/share}/figai/figai.sqlite` on Linux. WAL mode, foreign keys, a busy timeout, and numbered migrations are enabled. Event receipts expire after seven days; ordinary interaction metadata, sanitized action-journal rows, and resolved temporary directives expire after 30 days; unconfirmed skill previews expire after 24 hours. Explicit memories, user preferences, active temporary directives, active skills, skill audit history, reminder history, self-contained scheduled-task commands, and validated workflow plans and state persist. Slack conversation text, raw tool data, profile payloads, avatar data, private attachment URLs, and attachment bytes are not stored in interaction or action logs.

Backups are created daily under the platform's application-data `backups/` directory, with the newest seven retained. Asking FigAi for its status reports database and backup age.

To restore:

1. Stop the service with `launchctl bootout "gui/$UID/com.matgra.figai"` on macOS or `systemctl --user stop figai.service` on Linux.
2. Copy the current database and its `-wal`/`-shm` siblings somewhere safe.
3. Copy the selected backup over `figai.sqlite` and remove only the old database's `-wal` and `-shm` siblings.
4. Reload with `launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.matgra.figai.plist"` on macOS or `systemctl --user start figai.service` on Linux.

## Troubleshooting

- **Startup exits immediately:** inspect `~/Library/Logs/FigAi/stderr.log` on macOS or `journalctl --user-unit figai.service` on Linux; configuration is fail-closed and `.env` must be exactly mode `0600`.
- **No channel response:** confirm the channel ID is approved, the app is invited, and the message explicitly mentions FigAi.
- **DM denied:** the user must be an internal workspace member and belong to at least one approved channel. Membership/profile decisions cache for ten minutes.
- **Missing history or files:** reinstall the app after confirming the manifest scopes.
- **Socket disconnects:** check login state and connectivity; the platform service manager restarts crashed jobs.
- **OpenRouter failures:** verify account credits and model availability, or override model IDs in `.env`.
- **Brain unavailable:** confirm `OBSIDIAN_VAULT_PATH` points to a vault containing `.obsidian/`, `sources/`, `wiki/`, `maps/`, and `scripts/vault.py`, and make sure its Git working tree is clean before a write.
- **Linux Brain map unavailable:** install `rsvg-convert` (Debian/Ubuntu package `librsvg2-bin`) or ImageMagick.
