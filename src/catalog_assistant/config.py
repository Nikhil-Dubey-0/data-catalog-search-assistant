"""Runtime settings, read from environment variables (optionally a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_ROOT / "sql"
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_METADATA_DIR = DATA_DIR / "metadata"
CHANGE_BATCH_DIR = DATA_DIR / "changes" / "batch_002"
EVAL_FILE = DATA_DIR / "eval" / "eval_questions.csv"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional at runtime
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    var_dir: Path
    llm_provider: str = "offline"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_s: float = 30.0
    llm_max_retries: int = 2

    @property
    def warehouse_dir(self) -> Path:
        return self.var_dir / "warehouse"

    @property
    def catalog_db(self) -> Path:
        return self.var_dir / "catalog.db"

    @classmethod
    def from_env(cls, var_dir: Path | None = None) -> "Settings":
        _load_dotenv()
        env_var_dir = Path(os.getenv("CATALOG_VAR_DIR", "var"))
        if not env_var_dir.is_absolute():
            env_var_dir = PROJECT_ROOT / env_var_dir
        return cls(
            var_dir=var_dir or env_var_dir,
            llm_provider=os.getenv("LLM_PROVIDER", "offline").strip().lower() or "offline",
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip(),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip(),
            llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
            llm_model=os.getenv("LLM_MODEL", "").strip(),
            llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
            llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "30")),
            llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        )
