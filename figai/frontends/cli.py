"""Interactive terminal front-end. Same agent core as Slack; must work standalone
when Slack is down or misconfigured."""

from __future__ import annotations

from figai.agent.loop import Message
from figai.agent.session import AgentSession
from figai.config import Config
from figai.logging_setup import get_logger
from figai.skills_system.loader import SkillError, SkillLoader

logger = get_logger("cli")


def handle_skill_command(user_input: str, loader: SkillLoader) -> bool:
    """Handles `!skills` / `!approve <name>` directly, bypassing the model.
    Returns True if `user_input` was a skill command (handled either way)."""
    if user_input == "!skills":
        approved = loader.discover()
        pending = loader.list_pending()
        print("Approved skills:")
        for skill in approved:
            print(f"  - {skill.name}: {skill.description}")
        if pending:
            print("Pending approval (!approve <name>):")
            for name in pending:
                print(f"  - {name}")
        return True

    if user_input.startswith("!approve "):
        name = user_input.removeprefix("!approve ").strip()
        try:
            loader.approve(name)
            print(f"fig> Approved and activated skill '{name}'. Restart to load any new tools it adds.")
        except SkillError as exc:
            print(f"fig> {exc}")
        return True

    return False


def run_cli(config: Config) -> None:
    session = AgentSession(config)

    print(f"Fig CLI -- model: {config.app.models.default_chat}. Type 'exit' to quit, '!skills' to list skills.")
    conversation: list[Message] = []

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break
        if handle_skill_command(user_input, session.loader):
            continue

        conversation.append({"role": "user", "content": user_input})
        try:
            reply = session.loop.run(conversation)
        except Exception:
            logger.exception("Agent loop failed")
            print("fig> Something went wrong -- check logs/figai.log.")
            conversation.pop()
            continue

        conversation.append({"role": "assistant", "content": reply})
        print(f"fig> {reply}")
