"""take_screenshot: captures the screen(s) and asks the configured vision model to
describe what's on it. Only runs when explicitly asked -- never on a loop."""

from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import mss
import mss.tools

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.logging_setup import get_logger
from figai.tools.files import FileSender

logger = get_logger("tools.screenshot")

CaptureFn = Callable[[], Path]
DescribeFn = Callable[[Path, str], str]


def capture_screenshot() -> Path:
    """Captures all monitors combined into one PNG and returns its temp file path."""
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # index 0 is the union of all monitors
        shot = sct.grab(monitor)
        out_path = Path(tempfile.gettempdir()) / f"figai_screenshot_{int(time.time() * 1000)}.png"
        mss.tools.to_png(shot.rgb, shot.size, output=str(out_path))
        return out_path


def describe_image(client: Any, vision_model: str, image_path: Path, prompt: str) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = client.chat.completions.create(
        model=vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            }
        ],
    )
    return response.choices[0].message.content or ""


def register_screenshot_tool(
    registry: ToolRegistry,
    config: Config,
    client: Any,
    file_sender: FileSender | None = None,
    capture_fn: CaptureFn = capture_screenshot,
    describe_fn: DescribeFn | None = None,
) -> None:
    def _default_describe(path: Path, prompt: str) -> str:
        return describe_image(client, config.app.models.vision, path, prompt)

    describe = describe_fn or _default_describe

    def _take_screenshot(arguments: dict[str, Any]) -> str:
        prompt = arguments.get("question") or "Describe what's on the screen in concise detail."
        send_image = bool(arguments.get("send_image", False))

        path = capture_fn()
        try:
            description = describe(path, prompt)
            result: dict[str, Any] = {"description": description}

            if send_image:
                if file_sender is None:
                    result["send_image_error"] = "No file-delivery channel is connected right now."
                else:
                    try:
                        file_sender(path)
                        result["image_sent"] = True
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Failed to send screenshot image")
                        result["send_image_error"] = str(exc)

            return json.dumps(result)
        finally:
            path.unlink(missing_ok=True)

    registry.register(
        Tool(
            name="take_screenshot",
            description=(
                "Capture the current screen(s) and describe what's visible. Only call this when explicitly "
                "asked -- never proactively or on a loop."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "What to look for, if not a general description."},
                    "send_image": {"type": "boolean", "description": "Also send the raw screenshot image, if asked."},
                },
            },
            handler=_take_screenshot,
        )
    )
