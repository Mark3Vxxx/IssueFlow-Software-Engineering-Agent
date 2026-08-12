import json

import httpx

from issueflow.agent import DeepSeekModelClient


def test_deepseek_client_sends_tools_and_parses_tool_call():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["temperature"] == 0.0
        assert {tool["function"]["name"] for tool in payload["tools"]} == {
            "search",
            "read_file",
            "apply_patch",
            "run_tests",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"query":"Value"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 40,
                    "prompt_cache_miss_tokens": 60,
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = DeepSeekModelClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=http_client,
    )

    action = client.next_action("Negation is broken", history=[])

    assert action.tool == "search"
    assert action.arguments == {"query": "Value"}
    assert action.input_tokens == 100
    assert action.output_tokens == 20
    assert action.cost_usd > 0


def test_deepseek_client_sends_configured_temperature():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["temperature"] == 1.25
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "The repair is complete."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            },
        )

    client = DeepSeekModelClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        temperature=1.25,
    )

    client.next_action("Negation is broken", history=[])


def test_deepseek_client_parses_a_final_message_without_tool_call():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "The repair is complete."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = DeepSeekModelClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=http_client,
    )

    action = client.next_action("Negation is broken", history=[])

    assert action.tool is None
    assert action.message == "The repair is complete."


def test_deepseek_client_serializes_multiple_tool_calls_one_at_a_time():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Inspect likely locations.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"query":"additive"}',
                                    },
                                },
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"query":"inverse"}',
                                    },
                                },
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        )

    client = DeepSeekModelClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    action = client.next_action("Negation is broken", history=[])

    assert action.tool == "search"
    assert action.arguments == {"query": "additive"}


def test_deepseek_client_publishes_structured_patch_schema():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        apply_patch_tool = next(
            tool for tool in payload["tools"] if tool["function"]["name"] == "apply_patch"
        )
        parameters = apply_patch_tool["function"]["parameters"]
        assert parameters["required"] == ["path", "old_text", "new_text"]
        assert set(parameters["properties"]) == {"path", "old_text", "new_text"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "No edit required."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    client = DeepSeekModelClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.next_action("Negation is broken", history=[])


def test_deepseek_client_publishes_only_registered_test_commands():
    registered = (
        'python -c "assert broken()"',
        'python -c "assert fixed()"',
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        run_tests_tool = next(
            tool for tool in payload["tools"] if tool["function"]["name"] == "run_tests"
        )
        command_schema = run_tests_tool["function"]["parameters"]["properties"]["command"]
        assert command_schema["enum"] == list(registered)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "No test required."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            },
        )

    client = DeepSeekModelClient(
        api_key="test-key",
        model="deepseek-v4-flash",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        test_commands=registered,
    )

    client.next_action("Negation is broken", history=[])
