"""Shared wiring for the agent core (tools, skills, vault memory, system prompt)
used identically by every front-end -- CLI today, Slack next."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from figai.agent.client import build_client
from figai.agent.loop import AgentLoop
from figai.agent.prompt import build_system_prompt
from figai.agent.tools import ToolRegistry
from figai.brain_map.tools import register_brain_map_tool
from figai.config import Config
from figai.logging_setup import get_logger
from figai.media.tools import register_media_tools
from figai.obs.client import build_client_factory as build_obs_client_factory
from figai.obs.tools import register_obs_tools
from figai.reminders.context import ConversationContext
from figai.reminders.scheduler import Reminder, ReminderScheduler
from figai.reminders.tools import register_reminder_tools
from figai.skills_system.core_tools import register_skill_management_tools
from figai.skills_system.loader import SkillLoader
from figai.ssh.local_vault import LocalVaultBackend
from figai.ssh.memory import load_index_note, register_memory_tools
from figai.ssh.remote_vault import RemoteVaultBackend
from figai.ssh.run import register_ssh_run_tools
from figai.tools.clipboard import register_clipboard_tools
from figai.tools.confirm import ConfirmationStore
from figai.tools.fetch_url import register_fetch_url_tool
from figai.tools.files import FileSender, register_search_files_tool, register_send_file_tools
from figai.tools.media_keys import register_media_key_tool
from figai.tools.notifications import register_notification_tool
from figai.tools.programs import register_open_program_tool
from figai.tools.screenshot import register_screenshot_tool
from figai.tools.system_info import register_system_info_tools
from figai.tools.time_tool import register_time_tool
from figai.tools.usage import register_usage_tool
from figai.tools.web import register_open_url_tool
from figai.tools.web_search import register_web_search_tool
from figai.tools.window import register_active_window_tool

SKILLS_ROOT = Path("skills")
MessageSender = Callable[[str, str, "str | None"], None]
logger = get_logger("agent.session")


def build_remote_vault_factory(config: Config) -> Callable[[], RemoteVaultBackend]:
    def factory() -> RemoteVaultBackend:
        alias = config.app.vault.ssh_host_alias
        host = config.settings.ssh_hosts.get(alias)
        if host is None:
            raise OSError(f"SSH host alias '{alias}' is not configured in .env")
        return RemoteVaultBackend(host, config.app.vault.remote_path, config.app.vault.remote_writable_subfolder)

    return factory


def build_tool_registry(
    config: Config,
    loader: SkillLoader,
    confirmations: ConfirmationStore,
    remote_factory: Callable[[], RemoteVaultBackend],
    local_vault: LocalVaultBackend,
    client: Any,
    file_sender: FileSender | None,
    reminder_scheduler: ReminderScheduler,
    conversation_context: ConversationContext,
) -> ToolRegistry:
    registry = ToolRegistry()
    register_time_tool(registry)
    register_skill_management_tools(registry, loader)
    loader.register_all_tools(registry)

    register_ssh_run_tools(registry, config, confirmations)
    register_memory_tools(registry, config, remote_factory, local_vault, confirmations)

    register_system_info_tools(registry)
    register_usage_tool(registry, config)
    register_web_search_tool(registry, config)
    register_fetch_url_tool(registry)
    register_open_program_tool(registry, config)
    register_open_url_tool(registry)
    register_search_files_tool(registry, config)
    register_send_file_tools(registry, config, confirmations, file_sender)
    register_screenshot_tool(registry, config, client, file_sender=file_sender)

    register_media_tools(registry, config, confirmations)
    register_brain_map_tool(registry, remote_factory, local_vault, file_sender)

    register_clipboard_tools(registry)
    register_active_window_tool(registry)
    register_notification_tool(registry)
    register_media_key_tool(registry)
    register_obs_tools(registry, build_obs_client_factory(config))

    register_reminder_tools(registry, reminder_scheduler, conversation_context)

    return registry


class AgentSession:
    """Everything a front-end needs for one conversation: the agent loop, the
    skill loader (for skill-management commands), and the confirmation store
    (for front-ends that need to recognize a user's confirming reply)."""

    def __init__(
        self, config: Config, file_sender: FileSender | None = None, message_sender: MessageSender | None = None
    ) -> None:
        self.config = config
        self.loader = SkillLoader(SKILLS_ROOT)
        self.confirmations = ConfirmationStore(timeout_seconds=config.app.limits.confirmation_timeout_seconds)
        self.conversation_context = ConversationContext()

        def _on_reminder_fire(reminder: Reminder) -> None:
            if message_sender is None:
                logger.error("Reminder fired with no message-delivery channel configured: %s", reminder.message)
                return
            try:
                message_sender(reminder.channel, f":alarm_clock: Reminder: {reminder.message}", reminder.thread_ts)
            except Exception:
                logger.exception("Failed to deliver reminder %s", reminder.id)

        self.reminder_scheduler = ReminderScheduler(on_fire=_on_reminder_fire)

        client = build_client(config)
        remote_factory = build_remote_vault_factory(config)
        local_vault = LocalVaultBackend(
            Path(config.app.vault.local_path), config.app.vault.remote_writable_subfolder
        )
        registry = build_tool_registry(
            config,
            self.loader,
            self.confirmations,
            remote_factory,
            local_vault,
            client,
            file_sender,
            self.reminder_scheduler,
            self.conversation_context,
        )

        system_prompt = build_system_prompt(self.loader.get_summary_text())
        index_content, index_backend = load_index_note(remote_factory, local_vault, config.app.vault.index_note)
        if index_content:
            system_prompt += (
                f"\n\nYour saved memory (from the {index_backend} vault's "
                f"'{config.app.vault.index_note}'):\n{index_content}"
            )

        self.loop = AgentLoop(
            client=client,
            model=config.app.models.default_chat,
            tools=registry,
            system_prompt=system_prompt,
            max_iterations=config.app.limits.max_agent_iterations,
        )
