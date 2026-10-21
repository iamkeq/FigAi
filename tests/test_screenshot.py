from __future__ import annotations

import json
from pathlib import Path

from figai.agent.tools import ToolRegistry
from figai.tools.screenshot import register_screenshot_tool


def fake_capture(tmp_path: Path) -> Path:
    path = tmp_path / "shot.png"
    path.write_bytes(b"fake-png-bytes")
    return path


def test_take_screenshot_returns_description_and_cleans_up_file(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_screenshot_tool(
        registry,
        config=None,
        client=None,
        capture_fn=lambda: fake_capture(tmp_path),
        describe_fn=lambda path, prompt: f"described: {prompt}",
    )

    result = json.loads(registry.execute("take_screenshot", json.dumps({"question": "what app is open?"})))

    assert result == {"description": "described: what app is open?"}
    assert not (tmp_path / "shot.png").exists()


def test_take_screenshot_without_send_image_does_not_touch_file_sender(tmp_path: Path) -> None:
    sent: list[Path] = []
    registry = ToolRegistry()
    register_screenshot_tool(
        registry,
        config=None,
        client=None,
        file_sender=sent.append,
        capture_fn=lambda: fake_capture(tmp_path),
        describe_fn=lambda path, prompt: "a description",
    )

    registry.execute("take_screenshot", "{}")

    assert sent == []


def test_take_screenshot_with_send_image_uses_file_sender(tmp_path: Path) -> None:
    sent: list[Path] = []
    registry = ToolRegistry()
    register_screenshot_tool(
        registry,
        config=None,
        client=None,
        file_sender=sent.append,
        capture_fn=lambda: fake_capture(tmp_path),
        describe_fn=lambda path, prompt: "a description",
    )

    result = json.loads(registry.execute("take_screenshot", json.dumps({"send_image": True})))

    assert result["image_sent"] is True
    assert len(sent) == 1


def test_take_screenshot_send_image_without_sender_reports_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_screenshot_tool(
        registry,
        config=None,
        client=None,
        file_sender=None,
        capture_fn=lambda: fake_capture(tmp_path),
        describe_fn=lambda path, prompt: "a description",
    )

    result = json.loads(registry.execute("take_screenshot", json.dumps({"send_image": True})))

    assert "send_image_error" in result
