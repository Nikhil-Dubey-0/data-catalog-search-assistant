"""HTTP clients: request format, retries on transient errors, fast failure on permanent ones."""

from __future__ import annotations

import json

import httpx
import pytest

from catalog_assistant.config import Settings
from catalog_assistant.llm import LLMUnavailableError, create_client
from catalog_assistant.llm.clients import OllamaClient, OpenAICompatibleClient


def mock_http(responses: list, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_sends_schema_and_parses_message():
    seen = []
    http = mock_http([httpx.Response(200, json={"message": {"content": '{"keywords": ["a"]}'}})], seen)
    out = OllamaClient("http://local:11434/", "qwen2.5:3b", http=http).generate_json("sys", "usr", {"type": "object"})
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/api/chat"
    assert body["format"] == {"type": "object"} and body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert out == '{"keywords": ["a"]}'


def test_openai_compatible_sends_bearer_token_and_json_mode():
    seen = []
    http = mock_http([httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})], seen)
    client = OpenAICompatibleClient("https://api.example/v1", "m", "secret-key", http=http)
    assert client.generate_json("s", "u", {}) == "{}"
    assert seen[0].headers["authorization"] == "Bearer secret-key"
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}


def test_transient_errors_are_retried_then_succeed():
    seen = []
    http = mock_http([httpx.Response(503), httpx.ReadTimeout("slow"),
                      httpx.Response(200, json={"message": {"content": "{}"}})], seen)
    client = OllamaClient("http://x", "m", max_retries=2, http=http, backoff_s=0)
    assert client.generate_json("s", "u", {}) == "{}"
    assert len(seen) == 3


def test_permanent_error_is_not_retried():
    seen = []
    http = mock_http([httpx.Response(401, text="invalid api key"), httpx.Response(200, json={})], seen)
    client = OpenAICompatibleClient("https://api.example/v1", "m", "bad", max_retries=3, http=http, backoff_s=0)
    with pytest.raises(LLMUnavailableError, match="HTTP 401"):
        client.generate_json("s", "u", {})
    assert len(seen) == 1


def test_gives_up_after_max_retries():
    seen = []
    http = mock_http([httpx.ConnectError("refused")] * 3, seen)
    client = OllamaClient("http://x", "m", max_retries=2, http=http, backoff_s=0)
    with pytest.raises(LLMUnavailableError, match="3 attempt"):
        client.generate_json("s", "u", {})


def test_unexpected_response_shape_is_an_error():
    http = mock_http([httpx.Response(200, json={"error": "model not found"})], [])
    with pytest.raises(LLMUnavailableError, match="unexpected"):
        OllamaClient("http://x", "m", http=http).generate_json("s", "u", {})


def test_factory(tmp_path):
    s = Settings(var_dir=tmp_path)
    assert create_client(s, "offline") is None
    assert create_client(s, "ollama").name == "ollama:qwen2.5:3b"
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        create_client(s, "openai_compatible")
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_client(s, "gpt-magic")
