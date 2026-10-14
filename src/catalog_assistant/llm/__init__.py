"""LLM access, isolated behind :class:`LLMClient`. Use :func:`create_client` to get one."""

from __future__ import annotations

from ..config import Settings
from .base import LLMClient, LLMError, LLMUnavailableError
from .clients import OllamaClient, OpenAICompatibleClient

__all__ = ["LLMClient", "LLMError", "LLMUnavailableError", "create_client"]

PROVIDERS = ("offline", "ollama", "openai_compatible")


def create_client(settings: Settings, provider: str | None = None) -> LLMClient | None:
    """Return a configured client, or None for 'offline' (deterministic, no model calls)."""
    provider = (provider or settings.llm_provider or "offline").lower()
    if provider == "offline":
        return None
    if provider == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model,
                            settings.llm_timeout_s, settings.llm_max_retries)
    if provider == "openai_compatible":
        if not settings.llm_base_url or not settings.llm_model:
            raise ValueError("LLM_PROVIDER=openai_compatible needs LLM_BASE_URL and LLM_MODEL (see .env.example)")
        return OpenAICompatibleClient(settings.llm_base_url, settings.llm_model, settings.llm_api_key,
                                      settings.llm_timeout_s, settings.llm_max_retries)
    raise ValueError(f"Unknown LLM provider '{provider}'. Choose one of {PROVIDERS}.")
