"""Application configuration loaded from environment variables."""

from pathlib import Path
from urllib.parse import urlsplit
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


_CONFIG_FILE = Path(__file__).resolve()
PROJECT_ROOT = next(
    (parent for parent in _CONFIG_FILE.parents if (parent / ".env").exists()),
    Path.cwd(),
)


class Settings(BaseSettings):
    """Application settings."""

    # Official DeepSeek text model
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""

    # Database
    sqlite_path: str = "./data/offerpilot.db"
    admin_key: str = ""
    profile_signing_key: str = ""
    cors_origins: str = "http://localhost:3000"

    # Runtime assets
    knowledge_dir: str = "./resources/knowledge"
    rules_dir: str = "./resources/rules"

    # Independent embedding service
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

    @property
    def resolved_rules_dir(self) -> Path:
        path = Path(self.rules_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def allowed_origins(self) -> list[str]:
        origins: list[str] = []
        for raw_origin in self.cors_origins.split(","):
            origin = raw_origin.strip()
            if not origin or origin in {"*", "null"}:
                raise RuntimeError("OFFERPILOT_CORS_ORIGINS must contain explicit HTTP(S) origins")
            try:
                parsed = urlsplit(origin)
                _ = parsed.port
            except ValueError as exc:
                raise RuntimeError(f"Invalid CORS origin: {origin}") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise RuntimeError(f"Invalid CORS origin: {origin}")
            normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
            if normalized not in origins:
                origins.append(normalized)
        if not origins:
            raise RuntimeError("OFFERPILOT_CORS_ORIGINS must not be empty")
        return origins

    def validate_runtime_security(self) -> None:
        origins = self.allowed_origins
        if not self.debug and len(self.profile_signing_key.encode("utf-8")) < 32:
            raise RuntimeError("OFFERPILOT_PROFILE_SIGNING_KEY must be at least 32 bytes outside debug mode")
        if not self.debug and any(not origin.startswith("https://") for origin in origins):
            raise RuntimeError("Production CORS origins must use HTTPS because profile cookies are Secure")


settings = Settings()
