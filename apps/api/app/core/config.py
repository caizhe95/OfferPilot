"""Application configuration loaded from environment variables."""

import os
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # OpenAI
    openai_api_key: str = "sk-xxx"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # Agent
    agent_port: int = 3001
    agent_host: str = "0.0.0.0"

    # Database
    sqlite_path: str = "./data/offerpilot.db"

    # Knowledge
    knowledge_dir: str = "./knowledge/selected"

    # ASR
    asr_provider: str = "openai"
    asr_model: str = "whisper-1"

    # Logging
    log_level: str = "INFO"

    # Debug
    debug: bool = False
    mock_agent: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("debug", "mock_agent", mode="before")
    @classmethod
    def parse_bool_like(cls, value):
        """Parse common bool-ish env values and tolerate unrelated values."""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "debug", "development"}:
            return True
        if text in {"0", "false", "no", "off", "release", "production", ""}:
            return False
        return False

    @property
    def db_path(self) -> Path:
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
