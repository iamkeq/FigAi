"""The agent loop: send messages+tools to the model, execute tool calls, repeat."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from figai.agent.tools import ToolRegistry
from figai.logging_setup import get_logger

logger = get_logger("agent.loop")

Message = dict[str, Any]

FALLBACK_MESSAGE = "I hit my step limit working on that without reaching a final answer."
EXPLAIN_PROMPT = (
    "You've used all your steps. Do not call any tools. In at most 3 short sentences tell the user what you "
    "tried, exactly where you got stuck (quote the latest error if there was one), and what you need from "
    "them or what smaller step to try."
)


@dataclass
class AgentLoop:
    client: Any  # an OpenAI-compatible client (OpenRouter via the OpenAI SDK)
    model: str
    tools: ToolRegistry
    system_prompt: str
    max_iterations: int = 10

    def run(self, conversation: list[Message]) -> str:
        """Runs the loop over `conversation` (user/assistant turns, no system message)
        and returns the model's final text answer. Mutates nothing in `conversation`."""
        messages: list[Message] = [{"role": "system", "content": self.system_prompt}, *conversation]
        tool_schemas = self.tools.to_openai_tools()
        attempts: list[tuple[str, str]] = []

        for iteration in range(1, self.max_iterations + 1):
            logger.debug("Agent loop iteration %d/%d", iteration, self.max_iterations)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tool_schemas or None,
                tool_choice="auto" if tool_schemas else None,
            )
            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None)

            if not tool_calls:
                return message.content or ""

            messages.append(_assistant_message_with_tool_calls(message, tool_calls))
            for call in tool_calls:
                result = self.tools.execute(call.function.name, call.function.arguments)
                attempts.append((call.function.name, result))
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        logger.warning("Agent loop hit max_iterations=%d without a final answer", self.max_iterations)
        return self._explain_step_limit(messages, attempts)

    def _explain_step_limit(self, messages: list[Message], attempts: list[tuple[str, str]]) -> str:
        """Says what was tried and where it stuck, instead of a bare "try a smaller step".
        Asks the model for a short explanation; if that call fails (or tries to use tools
        again) falls back to a deterministic summary built from the tool results."""
        try:
            response = self.client.chat.completions.create(
                model=self.model, messages=[*messages, {"role": "user", "content": EXPLAIN_PROMPT}]
            )
            message = response.choices[0].message
            if not getattr(message, "tool_calls", None) and message.content:
                return f"{FALLBACK_MESSAGE}\n{message.content}"
        except Exception:  # noqa: BLE001 - the explanation is best-effort
            logger.exception("Could not get the model to explain the step limit")
        return f"{FALLBACK_MESSAGE}\n{_summarize_attempts(attempts)}"


def _last_error(attempts: list[tuple[str, str]]) -> str | None:
    for name, result in reversed(attempts):
        try:
            parsed = json.loads(result)
        except ValueError:
            continue
        if isinstance(parsed, dict) and parsed.get("error"):
            return f"{name}: {str(parsed['error'])[:300]}"
    return None


def _summarize_attempts(attempts: list[tuple[str, str]]) -> str:
    if not attempts:
        return "I didn't get as far as using any tools. Try a smaller step."
    tried = ", ".join(f"{name} x{count}" for name, count in Counter(n for n, _ in attempts).most_common())
    error = _last_error(attempts)
    tail = f" Latest error -- {error}." if error else ""
    return f"Tools I tried: {tried}.{tail} Try a smaller or more specific step."


def _assistant_message_with_tool_calls(message: Any, tool_calls: list[Any]) -> Message:
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in tool_calls
        ],
    }
