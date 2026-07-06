from pydantic import Field
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

    hn_api_url: str = "https://hacker-news.firebaseio.com/v0"
    hn_web_item_url_template: str = "https://news.ycombinator.com/item?id={item_id}"
    hn_text_max_chars: int = Field(default=5000, gt=0)

    signal_title_max_chars: int = Field(default=500, gt=0)
    signal_excerpt_max_chars: int = Field(default=5000, gt=0)
    signal_url_max_chars: int = Field(default=4096, gt=0)
    signal_metadata_max_bytes: int = Field(default=65536, gt=0)
    signal_hash_version: str = Field(default="v1")
    signal_default_source_quality_score: float = Field(default=0.5, ge=0.0, le=1.0)

    scoring_version: str = Field(default="v1", min_length=1, max_length=50)
    scoring_default_top_limit: int = Field(default=3, ge=1, le=20)
    scoring_max_opportunities: int = Field(default=1000, ge=1, le=10000)
    scoring_snapshot_history_limit: int = Field(default=20, ge=1, le=100)

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
