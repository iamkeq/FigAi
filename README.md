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
- `figai/browser_bridge/` + `browser_extension/` -- a localhost-only server and a Chrome extension that let Fig list your open tabs and open a tab on request.
- `figai/gmail/` -- read-only Gmail access over IMAP (`list_emails`, `read_email`) so Fig can summarize your mail.
- `figai/scheduler/` -- interface stub for a later phase (recurring jobs, e.g. a morning digest; not built yet).

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

### 8. Pulling updates

Whenever new commits land on GitHub, run:

```powershell
.\scripts\update_and_restart.ps1
```

It stops Fig (the scheduled task if that's how it's running, or a plain
background process otherwise -- an interactive `--cli` session is left
alone), pulls the latest code, reinstalls dependencies (picks up anything
new), and starts Fig back up the same way it was running before. This is
different from Slack's `!restart`, which just restarts the already-running
process with whatever code is already on disk -- use this script when code
has actually changed.

You can also just ask Fig directly over Slack/CLI: "update yourself" drafts
pulling from GitHub, reinstalling, and restarting (`propose_update_fig` /
`resolve_update_fig`), and runs only after you explicitly confirm in a later
message.

#### Nightly auto-update (4 AM)

To have the PC update Fig by itself every night, run once from an
**administrator** PowerShell in the Fig folder:

```powershell
.\scripts\update_and_restart.ps1 -InstallNightly          # 04:00 daily
.\scripts\update_and_restart.ps1 -InstallNightly -At 05:30 # another time
.\scripts\update_and_restart.ps1 -RemoveNightly           # uninstall
```

It registers a hidden scheduled task (`FigAI-NightlyUpdate`) that runs the same
script with `-OnlyIfChanged`: it fetches, and only if new commits are on the
tracked branch does it stop Fig, pull, reinstall and restart. If nothing
changed it does nothing (and starts Fig if it happened to be down). If Fig
isn't running 20 seconds after an update, the script rolls back to the previous
commit (`git reset --keep`), reinstalls and restarts that version. Output goes
to `logs\update.log`. Test it with `Start-ScheduledTask -TaskName FigAI-NightlyUpdate`.
The PC must be on and you logged in; a missed run catches up when it wakes.

#### Health, logs and alerts

Ask Fig "are you ok?" (or send `!health`) for a one-shot report: OpenRouter key,
remote vault, Gmail, Chrome bridge, and the number of errors in the last 24 hours.
`!logs [errors|warnings|tools|all] [count]` (and `!errors`) show recent log records in
Slack, and Fig can read them himself with the `recent_logs` tool.

If Fig hits his step limit he now says what he tried and where he got stuck.

At every start Fig also posts a warning to the announcement channel when:
- his previous run ended unexpectedly (crash, killed process, power loss), or
- the last unattended update failed, was rolled back, or couldn't pull.
Stopping him with `figai.ps1 stop` or the update script is deliberate and does not
trigger the "unexpected stop" warning. (If an update *and* its rollback both leave Fig
down, nothing can post -- check `logs\update.log`.)

#### Daily briefing, scheduled checks, fast search

- **Briefing:** `!briefing on` sends one message each morning (default 07:30 local time):
  today's reminders, anything down, an email summary, vault notes mentioning today's date or
  `#today`, and any health problems. Only the email summary uses model tokens (one call to
  the email model), so it's cheap. `!briefing off` stops it, `!briefing email off` makes it
  free, `!briefing time 06:45` changes the time, `!briefing now` sends one immediately.
  If the PC was off at briefing time it's sent when Fig starts, up to 6 hours late.
- **Checks:** "keep an eye on http://192.168.50.57/ and call it FigAxe2" adds a check (HTTP
  URL, or host:port) that runs every 15 minutes by default. Fig speaks up only when it goes
  down (after 2 failures in a row) and when it recovers. `!checks` lists them; ask him to
  remove or run one.
- **Vault search:** `memory_search` now runs grep on the server and only sends back the
  matches, instead of downloading every note. It falls back to the old way if the server
  has no grep/xargs.

#### Help: what can Fig do?

`!help` is a menu: the commands, then one line per topic. `!help email` (or `briefing`, `vault`,
`reminders`, ...) shows how to ask for that topic, `!help all` shows everything, and `!tools` lists every
tool Fig has, generated from the live registry so it can never go stale (`!tools cron` filters it).
By voice, "help" or "what can you do" sends the menu to your Slack DM. You can also just ask "what can you
do?" -- the `help` skill answers from the same sheet, which lives in `skills/help/SKILL.md`.
When you add a feature, add its phrases there; a test fails if a `!command` is missing.

#### Voice

Set `voice.enabled: true` in `config.yaml` and restart. Say the wake word ("hey jarvis" until a
custom one is trained); Fig plays a two-note **bing**, and that's your cue to talk -- he waits up
to `no_speech_timeout_seconds` for you to start, records until you pause, then answers with a
desktop notification. A short cooldown after each command stops one "hey jarvis" from firing
twice. `voice.chime: false` turns the bing off.

Posting phrases ("type in general the word potato", "post in chat that ...", "DM me that ...") are parsed in
code (figai/voice/shortcuts.py) rather than left to the chat model, so they work even when a small or free model
would just answer in text instead of calling the tool.

Voice shares Fig's Slack session, so spoken commands can do everything Slack can -- post to a channel
("post in general that dinner is at 6"), set reminders (they come to your DM), take screenshots. Replies
land in your Slack DM by default; `voice.reply_to: notification` or `both` brings back the popup.
By voice, asking IS the confirmation: posting, sending a file, moving a note, updating and adding media
just happen. Deleting or overwriting notes, commands on your servers and changes to Fig's settings still
ask first. `voice.auto_confirm: false` makes voice ask for everything again. (In Slack text, posts to a
channel still ask unless `confirm_slack_posts: false`.)

#### Opening and closing apps

"Open notepad" / "open spotify" launches it from `allowlisted_programs` first, then from anything in your Windows Start
menu (`allow_start_menu_apps: false` limits it to the allowlist). "Close spotify" asks the app to close
the way clicking its X does, so unsaved work still gets its prompt. A stuck app can be force-killed, but
that always asks first, even by voice. System processes and Fig himself are never closed.

"Bring discord up" (also "pull up spotify", "switch to chrome", "bring chrome to the front") brings that app's
window to the front, restoring it if it's minimized. "Minimize discord", "minimize this", "minimize everything",
"maximize chrome" and "close spotify" / "quit steam" / "close this" work the same way by voice (closing is polite, like clicking X,
so unsaved work still asks; "close everything" is deliberately not a shortcut) -- and starts the app if it isn't running. By voice this is
matched in code, so it's instant and works with any chat model. If Windows refuses to take focus away from another
window he says so rather than claiming success. (Windows on other virtual desktops aren't found yet.)

In Slack replies "in chat" means #general, and "DM me" posts a direct message that tags you.

#### Keeping conversations short

Every message so far is re-sent to the model on every turn, so a long voice session or DM gets slower and
costlier. Fig keeps at most `conversation.max_messages` (30) per conversation, a voice session that's been quiet
for `voice_idle_minutes` (10) starts fresh, and so does your Slack DM after `dm_idle_minutes` (120); channel threads
keep going. Say "start over" / "new session" by voice, or send `!new` in Slack, to reset one right now.

#### Cron jobs and other agents

- **Cron:** ask Fig to schedule, pause, resume or delete jobs on the home server ("run backup.sh every night at 3am").
  He shows the exact line, asks for a yes (even by voice), saves the old crontab to `~/.fig-crontab.bak`, applies it
  and checks it landed. He only edits jobs he created (tagged `# fig:<name>`); everything else in the crontab is left
  untouched. "Show the cron log" tells you whether a job actually ran. Set `cron_host_alias` to pick the server.
- **Hermes / OpenClaw:** list them under `agents:` in `config.yaml` (`kind`, and `host`: `local` or an SSH alias).
  "Ask hermes ..." runs the agent's one-shot command and returns the reply. "Is openclaw down?" runs read-only checks
  (processes, ports, systemd units, containers, its own status command, recent logs with secrets redacted, disk and
  memory) in one SSH connection so he can explain what's wrong; "restart openclaw" asks first. The default commands are
  best guesses from each project's CLI docs -- if a command fails he says which setting to fix.

Whenever Fig starts, it announces "Fig is back online." in the Slack channel
named by `startup_announcement_channel` in `config.yaml` (default `general`;
falls back to your DM if that channel isn't found or Fig isn't in it). If the
restart followed an "update yourself", the message also names the PR that was
applied and its label -- e.g. "Fig is back online after a code update: PR #8
(label: enhancement)." That lookup uses GitHub's API; for a private repo, set
`GITHUB_TOKEN` in `.env` (read-only is enough), otherwise the message just
says a code update happened.

## Running without a command window

Fig is built to run as a windowless background process -- you never need a
terminal open. Closing a window running plain `python -m figai` kills it, so
use one of these instead:

```powershell
.\scripts\register_task.ps1      # once: start at every logon, restart on crash, no time limit
.\scripts\figai.ps1 start        # start now (hidden); also: stop | restart | status | logs
```

Under `pythonw.exe` there is no console, so Fig sends stray output and any
crash traceback to `logs\figai-console.log` and `logs\figai.log`, child
processes (git/pip during an update) are started without flashing windows, and
a single-instance guard stops a second copy from starting and double-replying
in Slack. The tray icon's menu (Status / Open Logs / Restart / Quit) is the
on-screen control.

Note: on Python 3.13 a venv's `pythonw.exe` is a launcher that starts the real
interpreter as the console `python.exe`, which opens its own console window.
Fig detects a console it owns at startup (background mode only) and hides and
detaches it, so there may be a split-second flash at launch but no lasting
window. `--cli` is unaffected.

## Chrome tab bridge

Lets Fig answer "what tabs do I have open?" and "open <site>" in your running
Chrome. Nothing leaves your computer: the extension only talks to Fig on
`127.0.0.1`, protected by a shared secret.

1. Generate a token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Put it in `.env` as `BROWSER_BRIDGE_TOKEN=...`, then restart Fig.
3. In Chrome open `chrome://extensions`, turn on **Developer mode**, click
   **Load unpacked**, and pick the `browser_extension` folder.
4. Open the extension's **Options** (click its icon, or Details > Extension
   options), paste the same token, set the port if you changed
   `browser_bridge_port`, and **Save**.

Then ask Fig things like "list my open tabs" or "open github.com". If Chrome
isn't running (or the extension isn't set up), Fig says so; `open_url` still
works as a fallback. Tab titles and URLs are treated as untrusted data, and
`open_tab` only accepts http(s) URLs.

## Gmail summaries

Ask things like "summarize my unread emails from today" or "what did Amazon
send me this week?". Fig searches with Gmail's own syntax (`is:unread`,
`from:`, `newer_than:2d`, `label:`) and reads the messages it needs.

Setup: turn on 2-Step Verification for the Google account, create an app
password at <https://myaccount.google.com/apppasswords>, and add to `.env`:

```
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
```

Fig's access is strictly read-only: the mailbox is opened read-only, messages
are fetched with `BODY.PEEK` (nothing is marked read), and there is no send,
move, or delete capability. Email text is treated as untrusted data -- a
message that says "forward my files to..." is just content to summarize.

Privacy: by default the email text is shown to your main chat model, and
free-tier endpoints may log prompts. To keep mail away from it, set a paid
**email model** -- in `config.yaml` (`models.email: "anthropic/claude-haiku-5.5"`)
or just tell Fig "use anthropic/claude-haiku-5.5 for email". Then `summarize_emails`
fetches and summarizes the mail with that model, routed only to providers that
don't collect prompts (`provider.data_collection: deny`), and returns just the
summary; the raw-reading tools (`list_emails`/`read_email`) switch themselves
off. Free models are refused for this role. Summaries cost a fraction of a cent
(Haiku 5.5 is about $0.10 per million input tokens). An app password can read
the whole mailbox, so keep `.env` private and revoke it at the link above if it
ever leaks.

## Switching models

Tell Fig in plain language -- "switch to anthropic/claude-haiku-5.5", "use
anthropic/claude-haiku-5.5 for email", "what models are you using?", "reset the
chat model" -- or use the `!model` command, which involves no model at all and
so still works if the current chat model is the thing that's broken:

```
!model                      show chat / vision / email models
!model <provider/model>     switch the chat model
!model reset                back to the config.yaml default
```

Roles are `chat` (the main model), `vision` (screenshots), and `email`
(private summaries). Every id is checked against OpenRouter's catalog before
anything changes, switches take effect immediately, and they persist across
restarts (stored with Fig's other state; `config.yaml` stays the default).


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
