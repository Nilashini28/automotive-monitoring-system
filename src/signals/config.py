"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default=f"sqlite:///{PROJECT_ROOT / 'var' / 'signals.db'}")
    var_dir: Path = PROJECT_ROOT / "var"

    llm_provider: str = "rules"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    classify_prompt_version: str = "v1"
    llm_max_chars: int = 6000
    llm_temperature: float = 0.0

    http_timeout: int = 20
    user_agent: str = "AutomotiveSignalsBot/0.1"
    ingest_max_articles_per_source: int = 40
    ingest_interval_minutes: int = 60
    classify_interval_minutes: int = 15
    classify_batch_size: int = 20

    cors_origins: str = "http://localhost:8501"
    api_token: str = ""

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.var_dir.mkdir(parents=True, exist_ok=True)
    if settings.is_sqlite:
        db_path = settings.database_url.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return settings
