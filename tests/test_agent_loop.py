from __future__ import annotations

import json

from figai.agent.loop import FALLBACK_MESSAGE, AgentLoop
from figai.agent.tools import Tool, ToolRegistry
from tests.fakes import FakeClient, make_response, make_tool_call


def empty_registry() -> ToolRegistry:
    return ToolRegistry()


def registry_with_echo_tool() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo",
            description="Echoes back the given text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=lambda args: json.dumps({"echoed": args["text"]}),
        )
    )
    return registry


def test_loop_returns_final_answer_with_no_tool_calls() -> None:
    client = FakeClient([make_response(content="Hello there")])
    loop = AgentLoop(client=client, model="test/model", tools=empty_registry(), system_prompt="sys")

    result = loop.run([{"role": "user", "content": "hi"}])

    assert result == "Hello there"
    assert len(client.chat.completions.calls) == 1


def test_loop_executes_tool_call_then_returns_final_answer() -> None:
    tool_call = make_tool_call("call_1", "echo", json.dumps({"text": "ping"}))
    client = FakeClient(
        [
            make_response(content=None, tool_calls=[tool_call]),
            make_response(content="Done: ping"),
        ]
    )
    loop = AgentLoop(client=client, model="test/model", tools=registry_with_echo_tool(), system_prompt="sys")

    result = loop.run([{"role": "user", "content": "echo ping"}])

    assert result == "Done: ping"
    assert len(client.chat.completions.calls) == 2

    second_call_messages = client.chat.completions.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert json.loads(tool_messages[0]["content"]) == {"echoed": "ping"}


def test_loop_stops_at_max_iterations_instead_of_looping_forever() -> None:
    tool_call = make_tool_call("call_1", "echo", json.dumps({"text": "again"}))
    client = FakeClient([make_response(content=None, tool_calls=[tool_call])])
    loop = AgentLoop(
        client=client,
        model="test/model",
        tools=registry_with_echo_tool(),
        system_prompt="sys",
        max_iterations=3,
    )

    result = loop.run([{"role": "user", "content": "loop forever"}])

    assert result.startswith(FALLBACK_MESSAGE)
    assert "echo x3" in result
    assert len(client.chat.completions.calls) == 4  # 3 steps + the explanation attempt


def test_step_limit_uses_the_models_explanation_when_it_gives_one() -> None:
    tool_call = make_tool_call("call_1", "echo", json.dumps({"text": "again"}))
    client = FakeClient(
        [make_response(content=None, tool_calls=[tool_call])] * 2 + [make_response(content="I couldn't find the folder.")]
    )
    loop = AgentLoop(client=client, model="m", tools=registry_with_echo_tool(), system_prompt="s", max_iterations=2)

    result = loop.run([{"role": "user", "content": "x"}])

    assert result == f"{FALLBACK_MESSAGE}\nI couldn't find the folder."


def test_step_limit_summary_quotes_the_latest_tool_error() -> None:
    registry = ToolRegistry()
    registry.register(
        Tool(name="memory_list", description="d", parameters={"type": "object", "properties": {}},
             handler=lambda _a: json.dumps({"error": "No such folder"}))
    )
    call = make_tool_call("c", "memory_list", "{}")
    client = FakeClient([make_response(content=None, tool_calls=[call])])
    loop = AgentLoop(client=client, model="m", tools=registry, system_prompt="s", max_iterations=2)

    result = loop.run([{"role": "user", "content": "x"}])

    assert "memory_list x2" in result and "No such folder" in result


def test_loop_passes_tool_schemas_to_the_model() -> None:
    client = FakeClient([make_response(content="ok")])
    loop = AgentLoop(client=client, model="test/model", tools=registry_with_echo_tool(), system_prompt="sys")

    loop.run([{"role": "user", "content": "hi"}])

    sent_tools = client.chat.completions.calls[0]["tools"]
    assert sent_tools[0]["function"]["name"] == "echo"


def test_loop_omits_tools_param_when_no_tools_registered() -> None:
    client = FakeClient([make_response(content="ok")])
    loop = AgentLoop(client=client, model="test/model", tools=empty_registry(), system_prompt="sys")

    loop.run([{"role": "user", "content": "hi"}])

    call_kwargs = client.chat.completions.calls[0]
    assert call_kwargs["tools"] is None
    assert call_kwargs["tool_choice"] is None
