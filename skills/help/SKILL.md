---
name: help
description: Cheat sheet of every command and example phrase Fig understands. Use when the user asks what you can do, for help, a list of commands, keywords, triggers, or how to ask for something.
---

# Help: what Fig understands

Two kinds of triggers. **`!commands`** are exact and instant (no model involved, so they work even if the model is broken). Everything else is **plain English** -- there are no magic keywords, so the phrases below are just examples of what works. Anything that changes something asks for a yes first.

When the user asks for help, show the relevant section(s) below, not the whole sheet unless they ask for everything.

## Commands
- `!help` -- this menu; `!help <topic>` one topic (e.g. `!help email`); `!help all` everything
- `!tools [word]` -- every tool I have, auto-generated (e.g. `!tools cron`)
- `!status` -- online, uptime, model, last error
- `!health` -- check OpenRouter, vault, Gmail, Chrome bridge, recent errors
- `!logs [errors|warnings|tools|all] [count]` and `!errors` -- recent log records
- `!model` -- show models; `!model <id>` switch chat model; `!model reset`
- `!briefing` -- status; `on`, `off`, `now`, `time 07:30`, `email on|off`
- `!checks` -- list scheduled up/down checks
- `!skills` and `!approve <name>` -- list skills; approve a drafted one
- `!update` (or `update yourself -y`) -- update right now, no confirmation needed
- `!new` (or `!reset`) -- forget this conversation and start fresh
- `!restart` -- restart Fig (does not pull new code)

## Updates
- "update yourself" -- pull the latest code, reinstall, restart (asks first)
- "update yourself -y" / `!update` -- same, but skips the yes
- "what version are you?" / "what was the last PR?"

## Models
- "what models are you using?" / "switch to <model id>"
- "use <model id> for email" / "use <model id> for vision" / "reset the chat model"
- Email model must be a paid model (never `:free`).

## Email (read-only)
- "summarize my last 5 emails" / "anything urgent in my email today?"
- "emails from <name> this week" / "unread emails from today"
- With an email model set, only a summary reaches the chat model.

## Vault notes
- "add a note to the vault: <text>" / "write a note called <name> in <folder>"
- "search the vault for <text>" / "what's in the <folder> folder?" / "read the note <name>"
- "move <note> to <folder>" / "rename <note> to <name>" (asks first)
- "delete <note>" (asks first; recoverable from `.fig-trash`)
- "export the brain map" -- picture of how your notes link

## Reminders
- "remind me in 30 minutes to <thing>" -- tags you
- "remind me in 2 hours in #general to <thing>" / "list my reminders" / "cancel reminder 3"

## Checks (is it up?)
- "keep an eye on http://192.168.50.57/ and call it FigAxe2" (or `host:port`)
- "check FigAxe2 now" / "list my checks" / "stop monitoring FigAxe2"
- Alerts only when it goes down (2 failed checks in a row) and when it recovers.

## Briefing
- "turn the daily briefing on" / "turn it off" / "send the briefing now"
- "send the briefing at 6:45" / "leave email out of the briefing"
- Off by default; only the email part uses tokens.

## Health and logs
- "are you ok?" / "is everything working?" / "show recent errors" / "show your last tool calls"
- Fig also warns in Slack after an unexpected stop or a failed nightly update.

## Settings
- "show your config" / "set the vault ssh alias to <name>" / "change the startup channel to <name>"
- Can't change API keys, tokens, passwords, allowlists or limits -- edit those by hand.

## Web
- Links come back as short clickable labels (the real URL is underneath); short ones stay as they are.
- "search the web for <query>" / "read <url>" / "open <url>" / "how much OpenRouter credit is left?"

## This PC
- "take a screenshot" / "what's on my clipboard?" / "copy <text> to the clipboard"
- "what window is active?" / "battery?" / "network status" / "system status" / "top 5 programs by memory"
- "show a notification: <text>" / "play", "pause", "next track", "volume up", "mute"
- "bring discord up" / "pull up spotify" / "switch to chrome" -- brings that window to the front (restores it if minimized; starts the app if it isn't open)
- "minimize discord" / "minimize this" / "minimize everything" / "maximize chrome"
- "what windows do I have open?"
- "open notepad" / "open spotify" / "open calculator" / "open steam" (by voice it's instant; a name it can't find as an app goes to the model, e.g. a website) -- allowlisted programs, then anything in your Start menu
- "close spotify" / "close chrome" -- closes it politely (like clicking X); if it won't close, "force close it" (always asks)
- Never closes system processes or Fig himself
- "find files named <text>"
- "send me <file>" (asks first)

## Browser
- "what tabs do I have open?" / "open <url> in a new tab" (needs the Chrome extension)

## Media servers
- "sonarr queue" / "radarr library" / "sabnzbd status"
- "add the movie <title> (<year>)" (asks first)

## OBS
- "obs status" / "start recording" / "stop recording" / "start streaming" / "stop streaming"
- "switch OBS to the <name> scene"

## Cron jobs (on the home server)
- "what cron jobs are on turbo-plex?" / "show the cron log"
- "run /home/dave/backup.sh every night at 3am" / "pause the backup job" / "resume it" / "delete the backup job"
- Always shows the exact change and asks first (even by voice), backs up the old crontab, and only touches jobs Fig created (tagged `# fig:<name>`).

## Other agents (Hermes, OpenClaw)
- "ask hermes what it's working on" / "tell openclaw to check the weather" -- runs the agent's own command and returns its reply
- "is openclaw down? why?" -- read-only checks (processes, ports, services, status command, logs, disk/memory) and a diagnosis
- "restart openclaw" -- asks first; by voice it just happens
- Set up under `agents:` in config.yaml (which machine, and the commands if yours differ from the defaults).

## Servers and learning
- "run <command> on the server" (asks first, shows the exact command)
- "learn how to <procedure>" -- drafts a skill; nothing runs until you `!approve` it

## Posting to Slack
- "post in general that dinner is at 6" / "tell #dev the build is done"
- "in chat" / "in the chat" / "in general chat" all mean #general
- "DM me ..." / "message me ..." / "tell me ..." -- a direct message that tags you
- In Slack text, channel posts ask for a yes first (`confirm_slack_posts: false` skips it). By voice they just happen.

## Voice
- Say the wake word, wait for the bing, then talk. Replies come to your Slack DM (`voice.reply_to`: slack, notification or both).
- Posting phrases are matched in code, so they work with any chat model: "type in general the word potato", "post in chat that dinner is at six", "post to the dev channel that the build is done", "DM me that the miner is up".
- Spoken commands can do everything Slack can: post, remind, screenshot, vault notes.
- Asking out loud IS the yes: posting, sending a file, moving a note, updating, adding media just happen.
  Still asks first: deleting or overwriting notes, commands on your servers, changing Fig's settings.
  (`voice.auto_confirm: false` makes voice ask for everything.)

## Fresh starts
- Long conversations are trimmed (oldest messages first) and idle ones start over: voice after 10 minutes quiet, your DM after 2 hours.
- By voice: "start over" / "new session" / "forget everything". In Slack: `!new`.
- Tune in config.yaml under `conversation:` (max_messages, voice_idle_minutes, dm_idle_minutes; 0 = off).

## Slack basics
- DM Fig anytime. In a channel, @-mention him and keep talking in that thread.
- "what channels can you see?"
- Voice wake word is off by default (`voice.enabled` in config.yaml).
