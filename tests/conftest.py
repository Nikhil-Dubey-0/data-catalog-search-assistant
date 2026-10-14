from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catalog_assistant import db  # noqa: E402
from catalog_assistant.config import Settings  # noqa: E402
from catalog_assistant.llm.base import LLMClient  # noqa: E402
from catalog_assistant.pipeline import run_build  # noqa: E402
from catalog_assistant.warehouse import generate_warehouse  # noqa: E402


def build_catalog(var_dir: Path, metadata_dirs=None) -> Settings:
    settings = Settings(var_dir=var_dir)
    generate_warehouse(settings.warehouse_dir)
    result = run_build(settings, metadata_dirs)
    assert result.status == "success"
    return settings


@pytest.fixture(scope="session")
def base_settings(tmp_path_factory) -> Settings:
    """A catalog built once from the base metadata; tests must not modify it."""
    return build_catalog(tmp_path_factory.mktemp("base"))


@pytest.fixture()
def con(base_settings):
    c = db.connect(base_settings.catalog_db)
    yield c
    c.close()


@pytest.fixture()
def fresh_settings(tmp_path) -> Settings:
    """A private catalog for tests that change state."""
    return build_catalog(tmp_path / "var")


class ScriptedLLM(LLMClient):
    """Test double: returns queued responses (str, callable(user_prompt) or Exception) and records prompts."""

    name = "scripted"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate_json(self, system: str, user: str, schema: dict) -> str:
        self.calls.append({"system": system, "user": user})
        r = self.responses.pop(0) if self.responses else "{}"
        if isinstance(r, Exception):
            raise r
        return r(user) if callable(r) else r


@pytest.fixture()
def scripted():
    return ScriptedLLM
