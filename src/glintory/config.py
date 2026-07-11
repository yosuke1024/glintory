from pydantic import Field, field_validator, model_validator
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

    web_max_form_bytes: int = Field(default=65536, ge=1024, le=1048576)
    web_csrf_cookie_name: str = Field(default="glintory_csrf", min_length=1)
    web_csrf_token_bytes: int = Field(default=32, ge=16, le=64)
    review_note_max_chars: int = Field(default=5000, ge=100, le=50000)
    review_reason_max_chars: int = Field(default=1000, ge=50, le=10000)
    evidence_search_per_page: int = Field(default=25, ge=10, le=100)

    collection_stale_after_minutes: int = Field(default=60, ge=5, le=1440)
    collection_history_per_page: int = Field(default=25, ge=10, le=100)
    collection_error_display_max_chars: int = Field(default=2000, ge=200, le=10000)
    collection_web_max_items: int = Field(default=500, ge=1, le=1000)

    scheduler_poll_seconds: int = Field(default=30, ge=5, le=300)
    scheduler_lease_seconds: int = Field(default=120, ge=30, le=1800)
    scheduler_heartbeat_seconds: int = Field(default=30, ge=5, le=300)
    scheduler_max_due_per_tick: int = Field(default=20, ge=1, le=100)
    scheduler_execution_stale_minutes: int = Field(default=60, ge=5, le=1440)

    schedule_min_interval_minutes: int = Field(default=15, ge=5, le=1440)
    schedule_max_interval_minutes: int = Field(default=10080, ge=5, le=525600)

    @model_validator(mode="after")
    def validate_scheduler_settings(self) -> "Settings":
        if self.scheduler_heartbeat_seconds * 2 >= self.scheduler_lease_seconds:
            raise ValueError("heartbeat_seconds * 2 must be less than lease_seconds")
        if self.schedule_max_interval_minutes < self.schedule_min_interval_minutes:
            raise ValueError("schedule_max_interval_minutes must be >= schedule_min_interval_minutes")
        return self

    @field_validator("collection_history_per_page")
    @classmethod
    def validate_history_per_page(cls, v: int) -> int:
        if v not in (10, 25, 50, 100):
            raise ValueError("history per page must be 10, 25, 50, or 100")
        return v

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
