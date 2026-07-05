from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    database_url: str = "sqlite:///./data/glintory.sqlite3"

    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2026-03-10"
    github_excerpt_max_chars: int = 2000

    http_connect_timeout_seconds: float = 5.0
    http_read_timeout_seconds: float = 20.0
    http_write_timeout_seconds: float = 10.0
    http_pool_timeout_seconds: float = 5.0
    http_max_retries: int = 3
    http_backoff_base_seconds: float = 0.5
    http_max_response_bytes: int = 5242880
    http_min_host_interval_seconds: float = 0.5
    http_max_redirects: int = 5
    http_user_agent: str = "Glintory/0.1"

    model_config = SettingsConfigDict(
        env_prefix="GLINTORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
