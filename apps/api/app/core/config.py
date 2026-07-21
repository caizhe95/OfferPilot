"""Application configuration loaded from environment variables."""

from pathlib import Path
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def db_path(self) -> Path:
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
