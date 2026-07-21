"""Application configuration loaded from environment variables."""

from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


_CONFIG_FILE = Path(__file__).resolve()
PROJECT_ROOT = next(
    (parent for parent in _CONFIG_FILE.parents if (parent / ".env").exists()),
    Path.cwd(),
)


class Settings(BaseSettings):
    """Application settings."""

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # OpenAI-compatible text LLM
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    # Database
    sqlite_path: str = "./data/offerpilot.db"
    admin_key: str = ""

    # Knowledge
    knowledge_dir: str = "./knowledge"

    # OpenAI-compatible embedding service
    embedding_provider: str = "openai-compatible"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_timeout_seconds: float = 30.0
    require_embedding: bool = True

    # MiMo ASR uses its native environment variable names.
    mimo_api_key: str = Field(default="", validation_alias=AliasChoices("MIMO_API_KEY"))
    mimo_base_url: str = Field(
        default="https://api.xiaomimimo.com/v1",
        validation_alias=AliasChoices("MIMO_BASE_URL"),
    )
    mimo_asr_model: str = Field(
        default="mimo-v2.5-asr",
        validation_alias=AliasChoices("MIMO_ASR_MODEL"),
    )

    # Logging
    log_level: str = "INFO"

    # Debug
    debug: bool = False

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "env_prefix": "OFFERPILOT_",
        "extra": "ignore",
    }

    @property
    def db_path(self) -> Path:
        path = Path(self.sqlite_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def resolved_knowledge_dir(self) -> Path:
        path = Path(self.knowledge_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path


settings = Settings()
