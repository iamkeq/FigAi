# FigAI ("Fig")

A personal background AI assistant for Windows. Fig runs silently at logon, talks
to you mostly through Slack DMs, and falls back to a terminal CLI when something's
broken. The model comes from [OpenRouter](https://openrouter.ai) via the OpenAI
SDK; memory lives in your Obsidian vault on another machine, reached over SSH.

## Architecture

- `figai/agent/` -- the model-agnostic agent loop, OpenRouter client, tool registry, and system prompt.
- `figai/frontends/` -- thin interfaces into that same core: `cli.py` (terminal), `slack_bot.py` / `slack_router.py` (Slack), `tray.py` (system tray), `voice.py` (wake-word voice control), `service.py` (background entry point).
- `figai/tools/` -- core tools: screenshots, system/battery/network info, OpenRouter usage, web search, fetching a specific URL, launching allowlisted programs, opening URLs, file search/send, clipboard, active window, desktop notifications, media-key control, and the shared confirmation primitive.
- `figai/ssh/` -- key-based SSH: `ssh_run`, and the remote/local Obsidian vault backends behind `memory_read/write/search`.
- `figai/obs/` -- OBS Studio control over its WebSocket server (recording/streaming/scenes).
- `figai/voice/` -- the wake-word listening loop (testable in isolation) plus real adapters for the microphone, openWakeWord, and a local Whisper model.
- `figai/reminders/` -- a one-off reminder scheduler (`set_reminder`/`list_reminders`/`cancel_reminder`) that posts back to Slack after a delay; state survives a restart (see `figai/persistence/`).
- `figai/persistence/` -- a small atomic JSON store Fig uses to persist active Slack threads, conversation histories, and pending reminders across restarts.
- `figai/skills_system/` -- the skill loader: discovers `skills/*/SKILL.md`, loads full instructions lazily, and keeps `skills/_pending/` inert until you `!approve` it.
- `skills/` -- built-in skills: `ssh`, `media` (Sonarr/Radarr/SABnzbd), `brain_map` (vault graph export), `obs` (recording/streaming/scenes).
- `figai/scheduler/`, `figai/email/`, `figai/browser_bridge/` -- interface stubs for later phases (not built yet).

## Setup

### 1. Prerequisites

- Windows, with Python 3.12+ on PATH.
- An OpenRouter API key.
- A Slack workspace where you can install a custom app.
- (Optional) A machine reachable over SSH that hosts your Obsidian vault.

### 2. Install

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

### 3. Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) -> **Create New App** -> **From an app manifest**.
2. Paste the contents of [`slack-manifest.yaml`](slack-manifest.yaml). It requests
   `chat:write`, `im:history`, `im:write`, `files:write`, `app_mentions:read`,
   `channels:history`, `groups:history`, `reactions:write`, `channels:read`,
   `groups:read`, subscribes to `message.im`, `message.channels`,
   `message.groups`, and `app_mention`, and
   enables Socket Mode.
3. Under **Socket Mode**, enable it and generate an app-level token with the
   `connections:write` scope -- this is your `SLACK_APP_TOKEN` (`xapp-...`).
4. Under **OAuth & Permissions**, install the app to your workspace and copy the
   **Bot User OAuth Token** -- this is your `SLACK_BOT_TOKEN` (`xoxb-...`).
5. DM the app from Slack at least once so it has a DM channel with you. In a
   channel, invite the app and @-mention it to start a thread -- Fig only
   replies to DMs and to threads it was explicitly mentioned into; it ignores
   ordinary channel chatter.
6. Find your own Slack member ID (your profile -> **More** -> **Copy member ID**) --
   this is `ALLOWED_SLACK_USER_ID`. Fig replies to this user ID only; every other
   sender is silently ignored.

### 4. Generate an SSH key for the vault machine (optional, for vault memory)

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519_figai_vault" -C "figai-vault"
```

Copy the `.pub` file's contents into that machine's `~/.ssh/authorized_keys`, and
make sure the vault host is already in Fig's `known_hosts`
(`ssh -i <key> user@host` once manually to accept its host key -- Fig refuses
unknown host keys rather than prompting).

### 5. Configure `.env` and `config.yaml`

```powershell
copy .env.example .env
copy config\config.example.yaml config\config.yaml
```

Edit `.env` (secrets: API keys, Slack tokens, your Slack user ID, SSH host
connection details, optional Sonarr/Radarr/SABnzbd credentials, optional OBS
WebSocket host/port/password) and `config\config.yaml` (models, the vault's
remote/local paths, allowlisted programs and folders, limits). Neither file is
ever logged.

A few tools (`active_window`, `media_key`) only have a real implementation on
Windows -- Fig's deployment target -- and return a clear "not supported"
error on any other OS rather than faking a value.

If the vault's remote SSH host is unreachable, Fig automatically falls back to a
fully independent local vault at `vault.local_path` -- the two are never merged,
so reconcile them yourself in Obsidian if that happens.

### 6. Run it

```powershell
# Debug in a terminal, with Slack optionally not even configured:
.venv\Scripts\python -m figai --cli

# Or run it the way it'll actually run, in the background:
.venv\Scripts\pythonw -m figai
```

### 7. Register it to start at logon

```powershell
.\scripts\register_task.ps1
```

This registers a Task Scheduler task that launches Fig with `pythonw.exe` (no
console window) at your next logon, runs only while you're logged on, and
restarts automatically (up to 5 times) if it crashes.

## Using Fig

- Slack: DM the bot. `!status` reports uptime/model/last error, `!restart`
  restarts the process, `!skills` lists skills, `!approve <name>` activates a
  pending skill. Fig reacts :eyes: to your message as soon as it starts
  working on it, and swaps that to :white_check_mark: once it's replied.
- Terminal: `python -m figai --cli` gives you the same commands plus live tool-call
  and error output, and works even when Slack is down or misconfigured.
- Confirmation flows: `ssh_run`, `send_file`, overwriting an existing vault note,
  and adding a Sonarr/Radarr title all draft the action first and need your
  explicit "yes" in a **later** message before anything actually happens.
- Skills Fig drafts for itself (when you ask it to learn something) are written
  to `skills/_pending/` and never activate until you `!approve` them.
- Reminders: "remind me in 20 minutes to check the oven" posts back to that
  same conversation (or thread) by default (and @-mentions you so Slack
  actually notifies you); name a channel (e.g. "post it to general") and Fig
  resolves it with `list_channels` first. Persisted to `state_path` in
  `config.yaml`, so a pending reminder survives a restart -- if Fig was down
  past its due time, it fires as soon as Fig starts back up.
- Active Slack threads and their conversation history also persist across a
  restart, so a follow-up message in a thread you'd previously @-mentioned
  Fig in still gets a reply without re-tagging it.
- `web_search` looks things up live via OpenRouter's web plugin (sources
  included) -- unlike everything else in Fig, this costs real OpenRouter
  credit per call. `fetch_url` reads one specific public page's text for free
  and refuses URLs that resolve to a private/internal address.
- Voice ("fig, open obs"): off by default. Enable it in `config.yaml` under
  `voice:` (`enabled: true`). It listens locally for a wake word, transcribes
  your command with a local Whisper model, and runs it through the same agent
  core as Slack/CLI -- Fig doesn't talk back yet, it acts and shows a desktop
  notification with its reply. openWakeWord ships pretrained wake words
  (`hey_jarvis`, `alexa`, `hey_mycroft`, ...) but no "fig" model yet; the
  default (`hey_jarvis`) works out of the box as a stand-in until you train a
  custom one with [openWakeWord's training notebook](https://github.com/dscripka/openWakeWord#training-new-models)
  and point `wake_word_model` at its file path. First run downloads the wake
  word and Whisper models (a few hundred MB).

## How to debug

- Logs: `logs/figai.log` (rotating, 5 x 5 MB). The tray icon's **Open Logs** menu
  item opens this folder.
- Run `python -m figai --cli` to see tool calls and errors live, independent of
  whether Slack is working.
- Restart: the tray icon's **Restart** item, `!restart` in Slack, or just
  `Stop-ScheduledTask` / `Start-ScheduledTask -TaskName FigAI`.
- Configuration errors (missing `.env`/`config.yaml` values) fail closed with a
  clear message on startup rather than running in a half-configured state.

## Tests

```powershell
.venv\Scripts\pytest
```

Covers the agent loop (mocked model), tool allowlists, the confirmation flow,
Slack user filtering, the skills pending/approval flow, and the vault's
remote-then-local fallback.

## What's next (designed for, not built in Phase 1)

- **Email summary**: `figai/email/interface.py` is a placeholder for a daily
  Gmail/IMAP summary posted to Slack on a schedule, once a scheduler
  (`figai/scheduler/`, likely APScheduler) is wired in.
- **Browser tab bridge**: `figai/browser_bridge/interface.py` is a placeholder for
  reading your open browser tabs via a small Chrome extension bridge.
