"""HTTP clients for free model providers: local Ollama and OpenAI-compatible APIs."""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

from .base import LLMClient, LLMUnavailableError

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def post_json_with_retry(client: httpx.Client, url: str, payload: dict, headers: dict | None,
                         max_retries: int, backoff_s: float = 1.0,
                         sleep: Callable[[float], None] = time.sleep) -> dict:
    """POST with exponential backoff on timeouts, connection errors, 429 and 5xx.

    Non-retryable HTTP errors (400/401/403/404...) fail immediately.
    """
    last_error = "unknown error"
    for attempt in range(max_retries + 1):
        try:
            resp = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            last_error = f"timeout: {exc}"
        except httpx.TransportError as exc:
            last_error = f"connection error: {exc}"
        else:
            if resp.status_code < 400:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise LLMUnavailableError(f"provider returned non-JSON body: {resp.text[:200]}") from exc
            if resp.status_code not in RETRYABLE_STATUS:
                raise LLMUnavailableError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
            last_error = f"HTTP {resp.status_code}"
        if attempt < max_retries:
            delay = backoff_s * (2 ** attempt)
            log.warning("LLM call failed (%s); retry %d/%d in %.1fs", last_error, attempt + 1, max_retries, delay)
            sleep(delay)
    raise LLMUnavailableError(f"giving up after {max_retries + 1} attempt(s): {last_error}")


class OllamaClient(LLMClient):
    """Local, free models via Ollama's /api/chat with schema-constrained output."""

    def __init__(self, base_url: str, model: str, timeout_s: float = 30.0, max_retries: int = 2,
                 http: httpx.Client | None = None, backoff_s: float = 1.0):
        self.name = f"ollama:{model}"
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.http = http or httpx.Client(timeout=timeout_s)

    def generate_json(self, system: str, user: str, schema: dict) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0},
        }
        data = post_json_with_retry(self.http, self.url, payload, None, self.max_retries, self.backoff_s)
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise LLMUnavailableError(f"unexpected Ollama response shape: {str(data)[:200]}") from exc


class OpenAICompatibleClient(LLMClient):
    """Any /chat/completions endpoint (Groq, Gemini OpenAI-compat, OpenRouter, vLLM, ...)."""

    def __init__(self, base_url: str, model: str, api_key: str, timeout_s: float = 30.0,
                 max_retries: int = 2, http: httpx.Client | None = None, backoff_s: float = 1.0):
        self.name = f"openai_compatible:{model}"
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.http = http or httpx.Client(timeout=timeout_s)

    def generate_json(self, system: str, user: str, schema: dict) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        data = post_json_with_retry(self.http, self.url, payload, headers, self.max_retries, self.backoff_s)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailableError(f"unexpected response shape: {str(data)[:200]}") from exc
