"""Runtime configuration read from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    token_secret: str
    token_ttl_seconds: int = 3600
    max_failed_logins: int = 5
    default_currency: str = "USD"


def load_settings() -> Settings:
    """Build settings from the environment; fail fast when a required value is missing."""
    try:
        return Settings(
            database_url=os.environ["FERNWOOD_DATABASE_URL"],
            token_secret=os.environ["FERNWOOD_TOKEN_SECRET"],
            token_ttl_seconds=int(os.environ.get("FERNWOOD_TOKEN_TTL", "3600")),
        )
    except KeyError as exc:
        raise RuntimeError(f"Missing required setting: {exc.args[0]}") from exc
