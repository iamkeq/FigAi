"""The agent loop: send messages+tools to the model, execute tool calls, repeat."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from figai.agent.tools import ToolRegistry
from figai.logging_setup import get_logger

logger = get_logger("agent.loop")

Message = dict[str, Any]

FALLBACK_MESSAGE = (
    "I hit my step limit working on that without reaching a final answer. "
    "Try breaking the request into a smaller step."
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
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        logger.warning("Agent loop hit max_iterations=%d without a final answer", self.max_iterations)
        return FALLBACK_MESSAGE


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
