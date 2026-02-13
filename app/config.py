from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Flight Search Service"
    api_prefix: str = "/v1"

    database_url: str = "sqlite+aiosqlite:///./flight_search.db"
    redis_url: str = "redis://localhost:6379/0"

    cache_ttl_seconds: int = 180
    connector_timeout_seconds: int = 20
    connector_retries: int = 1
    max_parallel_connectors: int = 4
    max_offers_per_search: int = 50
    default_sources: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["trip_com", "airasia"]
    )

    enable_browser_connectors: bool = True
    browser_headless: bool = True
    request_timeout_seconds: int = 15
    fx_rate_ttl_seconds: int = 1800

    trip_com_result_selector: str = "[data-testid^='u-flight-card-']"
    trip_com_link_selector: str = "a[href]"
    trip_com_wait_ms: int = 6000
    trip_com_max_cards: int = 30

    airasia_result_selector: str = "[data-testid*='flight'], article, .flight-item, .flightCard"
    airasia_link_selector: str = "a[href]"
    airasia_wait_ms: int = 3500
    airasia_max_cards: int = 20

    @field_validator("default_sources", mode="before")
    @classmethod
    def _parse_sources(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return ["trip_com", "airasia"]
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return [item.strip().lower() for item in value if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
