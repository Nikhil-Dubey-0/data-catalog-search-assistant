"""The single interface the rest of the code uses to talk to a language model.

Nothing outside ``catalog_assistant.llm`` knows about HTTP, providers or API keys.
Any model (local Ollama, a hosted free tier, a test double) just has to implement
``generate_json``: take a system + user prompt and a JSON schema, return raw text
that *should* be JSON. Callers must still validate it -- models are not trusted.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """Base class for model failures."""


class LLMUnavailableError(LLMError):
    """The model could not be reached or refused the request (network, auth, rate limit)."""


class LLMClient(ABC):
    name: str = "llm"

    @abstractmethod
    def generate_json(self, system: str, user: str, schema: dict) -> str:
        """Return the model's raw text response (expected to be a JSON object)."""
