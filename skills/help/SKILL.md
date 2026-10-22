---
name: help
description: Cheat sheet of every command and example phrase Fig understands. Use when the user asks what you can do, for help, a list of commands, keywords, triggers, or how to ask for something.
---

# Help: what Fig understands

Two kinds of triggers. **`!commands`** are exact and instant (no model involved, so they work even if the model is broken). Everything else is **plain English** -- there are no magic keywords, so the phrases below are just examples of what works. Anything that changes something asks for a yes first.

When the user asks for help, show the relevant section(s) below, not the whole sheet unless they ask for everything.

## Commands
- `!help [topic]` -- this sheet, or one section (e.g. `!help email`)
- `!status` -- online, uptime, model, last error
- `!health` -- check OpenRouter, vault, Gmail, Chrome bridge, recent errors
- `!logs [errors|warnings|tools|all] [count]` and `!errors` -- recent log records
- `!model` -- show models; `!model <id>` switch chat model; `!model reset`
- `!briefing` -- status; `on`, `off`, `now`, `time 07:30`, `email on|off`
- `!checks` -- list scheduled up/down checks
- `!skills` and `!approve <name>` -- list skills; approve a drafted one
- `!restart` -- restart Fig (does not pull new code)

## Updates
- "update yourself" -- pull the latest code, reinstall, restart (asks first)
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
- "open <program>" (only programs on the allowlist) / "find files named <text>"
- "send me <file>" (asks first)

## Browser
- "what tabs do I have open?" / "open <url> in a new tab" (needs the Chrome extension)

## Media servers
- "sonarr queue" / "radarr library" / "sabnzbd status"
- "add the movie <title> (<year>)" (asks first)

## OBS
- "obs status" / "start recording" / "stop recording" / "start streaming" / "stop streaming"
- "switch OBS to the <name> scene"

## Servers and learning
- "run <command> on the server" (asks first, shows the exact command)
- "learn how to <procedure>" -- drafts a skill; nothing runs until you `!approve` it

## Slack basics
- DM Fig anytime. In a channel, @-mention him and keep talking in that thread.
- "what channels can you see?"
- Voice wake word is off by default (`voice.enabled` in config.yaml).
