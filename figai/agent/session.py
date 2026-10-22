"""Shared wiring for the agent core (tools, skills, vault memory, system prompt)
used identically by every front-end -- CLI today, Slack next."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from figai.agent.client import build_client
from figai.agent.loop import AgentLoop
from figai.agent.prompt import build_system_prompt
import json

from figai.agent.tools import ToolRegistry
from figai.briefing.briefing import BriefingManager, BriefingSources, build_briefing
from figai.briefing.tools import register_briefing_tools
from figai.brain_map.tools import register_brain_map_tool
from figai.browser_bridge.state import BridgeState
from figai.browser_bridge.tools import register_browser_bridge_tools
from figai.config import Config
from figai.gmail.tools import register_email_tools
from figai.health import HealthChecker
from figai.logging_setup import get_logger
from figai.monitoring.checks import CheckManager
from figai.monitoring.tools import register_check_tools
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
from figai.tools.channels import ChannelLister, register_list_channels_tool
from figai.tools.clipboard import register_clipboard_tools
from figai.tools.config_edit import register_config_edit_tools
from figai.tools.confirm import ConfirmationStore
from figai.tools.diagnostics import register_diagnostics_tools
from figai.tools.fetch_url import register_fetch_url_tool
from figai.tools.files import FileSender, register_search_files_tool, register_send_file_tools
from figai.tools.media_keys import register_media_key_tool
from figai.tools.models import ModelManager, register_model_tools
from figai.tools.notifications import register_notification_tool
from figai.tools.programs import register_open_program_tool
from figai.tools.screenshot import register_screenshot_tool
from figai.tools.self_update import register_self_update_tools
from figai.tools.system_info import register_system_info_tools
from figai.tools.time_tool import register_time_tool
from figai.tools.usage import register_usage_tool
from figai.tools.version_info import register_version_info_tool
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
    channel_lister: ChannelLister | None,
    on_updated: Callable[[str, str], None] | None,
    bridge_state: BridgeState,
    models: ModelManager,
    health_report: Callable[[], str],
    checks: CheckManager,
    briefing: BriefingManager,
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
    register_list_channels_tool(registry, channel_lister)
    register_self_update_tools(registry, confirmations, on_updated=on_updated)
    register_version_info_tool(registry, config)
    register_browser_bridge_tools(registry, bridge_state, enabled=bool(config.settings.browser_bridge_token))
    register_email_tools(registry, config)
    register_model_tools(registry, models)
    register_config_edit_tools(registry, confirmations)
    register_diagnostics_tools(registry, health_report)
    register_check_tools(registry, checks, conversation_context, config.settings.allowed_slack_user_id)
    register_briefing_tools(registry, briefing)

    return registry


class AgentSession:
    """Everything a front-end needs for one conversation: the agent loop, the
    skill loader (for skill-management commands), and the confirmation store
    (for front-ends that need to recognize a user's confirming reply)."""

    def __init__(
        self,
        config: Config,
        file_sender: FileSender | None = None,
        message_sender: MessageSender | None = None,
        channel_lister: ChannelLister | None = None,
        on_updated: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.loader = SkillLoader(SKILLS_ROOT)
        self.confirmations = ConfirmationStore(timeout_seconds=config.app.limits.confirmation_timeout_seconds)
        self.conversation_context = ConversationContext()
        self.bridge_state = BridgeState()
        self.models = ModelManager(config, apply_chat=lambda model: setattr(self.loop, "model", model))

        def _on_reminder_fire(reminder: Reminder) -> None:
            if message_sender is None:
                logger.error("Reminder fired with no message-delivery channel configured: %s", reminder.message)
                return
            try:
                mention = f"<@{config.settings.allowed_slack_user_id}> "
                message_sender(reminder.channel, f"{mention}:alarm_clock: Reminder: {reminder.message}", reminder.thread_ts)
            except Exception:
                logger.exception("Failed to deliver reminder %s", reminder.id)

        self.reminder_scheduler = ReminderScheduler(on_fire=_on_reminder_fire)

        def _notify(channel: str, text: str) -> None:
            if message_sender is None:
                logger.error("No message channel configured; dropping: %s", text[:120])
                return
            message_sender(channel, text, None)

        self.checks = CheckManager(notify=_notify)

        client = build_client(config)
        remote_factory = build_remote_vault_factory(config)
        local_vault = LocalVaultBackend(
            Path(config.app.vault.local_path), config.app.vault.remote_writable_subfolder
        )
        self.health = HealthChecker(config, remote_factory, self.bridge_state, describe_models=self.models.describe)

        def _run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            try:
                return json.loads(self.loop.tools.execute(name, json.dumps(arguments)))
            except Exception as exc:  # noqa: BLE001 - a broken section must not sink the whole briefing
                return {"error": str(exc)}

        sources = BriefingSources(
            run_tool=_run_tool,
            pending_reminders=self.reminder_scheduler.list_pending,
            down_checks=lambda: [self.checks.describe(c) for c in self.checks.down()],
            health_problems=lambda: [
                f"{r.name}: {r.detail}" for r in self.health.run() if r.ok is False and r.name != "Chrome bridge"
            ],
        )
        self.briefing = BriefingManager(
            config.app.briefing,
            owner_channel=config.settings.allowed_slack_user_id,
            build=lambda settings, now: build_briefing(sources, settings, now),
            notify=_notify,
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
            channel_lister,
            on_updated,
            self.bridge_state,
            self.models,
            self.health.report,
            self.checks,
            self.briefing,
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
