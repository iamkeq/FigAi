from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from figai.config import AppConfig
from figai.frontends.slack_router import RESTART_SENTINEL, SlackRouter, is_authorized_message
from figai.skills_system.loader import SkillLoader


class FakeLoop:
    def __init__(self, reply: str = "ok", raise_error: bool = False) -> None:
        self.reply = reply
        self.raise_error = raise_error
        self.calls: list[list[dict]] = []

    def run(self, conversation: list[dict]) -> str:
        self.calls.append(list(conversation))
        if self.raise_error:
            raise RuntimeError("boom")
        return self.reply


def make_app_config(tmp_path: Path) -> SimpleNamespace:
    app = AppConfig.model_validate(
        {
            "models": {"default_chat": "test/model", "vision": "y"},
            "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path / "vault")},
        }
    )
    return SimpleNamespace(app=app)


def make_router(tmp_path: Path, reply: str = "ok", raise_error: bool = False) -> SlackRouter:
    loader = SkillLoader(tmp_path / "skills")
    session = SimpleNamespace(loop=FakeLoop(reply, raise_error), loader=loader)
    return SlackRouter(session=session, config=make_app_config(tmp_path), allowed_user_id="U_OWNER")


def test_is_authorized_message_accepts_only_the_allowed_user() -> None:
    assert is_authorized_message({"user": "U_OWNER"}, "U_OWNER") is True
    assert is_authorized_message({"user": "U_OTHER"}, "U_OWNER") is False


def test_is_authorized_message_ignores_bot_messages() -> None:
    assert is_authorized_message({"user": "U_OWNER", "bot_id": "B1"}, "U_OWNER") is False


def test_is_authorized_message_ignores_message_subtypes() -> None:
    assert is_authorized_message({"user": "U_OWNER", "subtype": "message_changed"}, "U_OWNER") is False


def test_handle_event_ignores_unauthorized_sender(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_event({"user": "U_OTHER", "text": "hi", "channel": "C1"}) is None


def test_handle_event_ignores_empty_text(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_event({"user": "U_OWNER", "text": "   ", "channel": "C1"}) is None


def test_handle_event_routes_authorized_message_to_agent_loop(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="hello back")

    reply = router.handle_event({"user": "U_OWNER", "text": "hi", "channel": "C1"})

    assert reply == "hello back"
    assert router.conversations["C1"][-1] == {"role": "assistant", "content": "hello back"}


def test_handle_event_agent_loop_error_rolls_back_conversation(tmp_path: Path) -> None:
    router = make_router(tmp_path, raise_error=True)

    reply = router.handle_event({"user": "U_OWNER", "text": "hi", "channel": "C1"})

    assert reply is not None and "went wrong" in reply.lower()
    assert router.conversations["C1"] == []
    assert router.last_error == "boom"


def test_status_command_reports_configured_model(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert "test/model" in router.handle_text("C1", "!status")


def test_restart_command_returns_sentinel_without_executing_anything(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_text("C1", "!restart") == RESTART_SENTINEL


def test_skills_command_lists_pending_skill(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.session.loader.draft_skill("helper", "helps", "do it")

    assert "helper" in router.handle_text("C1", "!skills")


def test_approve_command_activates_pending_skill(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.session.loader.draft_skill("helper", "helps", "do it")

    reply = router.handle_text("C1", "!approve helper")

    assert "Approved" in reply
    assert "helper" in [s.name for s in router.session.loader.discover()]


def test_conversations_are_isolated_per_channel(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="ok")

    router.handle_event({"user": "U_OWNER", "text": "hi from c1", "channel": "C1"})
    router.handle_event({"user": "U_OWNER", "text": "hi from c2", "channel": "C2"})

    assert len(router.conversations["C1"]) == 2
    assert len(router.conversations["C2"]) == 2
    assert router.conversations["C1"][0]["content"] == "hi from c1"
    assert router.conversations["C2"][0]["content"] == "hi from c2"
