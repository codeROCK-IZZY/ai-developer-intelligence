"""Application configuration from ``ADI_*`` environment variables (or a git-ignored ``.env``).

Nothing secret has a default: the app refuses to start without ``ADI_JWT_SECRET``.
The Anthropic key is read by the SDK from ``ANTHROPIC_API_KEY``; when it is absent the
platform runs in *offline* mode.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADI_", env_file=".env", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+psycopg://adi:adi@localhost:5432/adi"

    # --- security ---
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    login_rate_limit_per_minute: int = 10
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    # --- storage & repository limits ---
    file_dir: Path = Path("./var/files")
    s3_bucket: str | None = None
    max_archive_mb: int = 100  # upload or GitHub download
    archive_max_entries: int = 50_000
    archive_max_uncompressed_mb: int = 500
    max_file_kb: int = 512  # larger source files are skipped (usually generated)
    max_files: int = 5_000  # indexed files per snapshot

    # --- AI providers ---
    llm_provider: Literal["auto", "claude", "offline"] = "auto"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_fallbacks: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = 15
    # Search: re-ranking is opt-in. On the code benchmark the text-trained cross-encoder improved MRR
    # by only 0.02 while adding ~3 s per query on a laptop CPU. Q&A always re-ranks (it gates on it).
    rerank_by_default: bool = False
    embedding_cache_dir: str | None = None  # baked into the Docker image at build time
    # ONNX Runtime threads per process. Default (all cores) oversubscribes the CPU when the API
    # and worker run side by side — measured ~10x slower in docker compose. None = library default.
    embedding_threads: int | None = None

    # --- GitHub import (public repositories, via codeload.github.com archives) ---
    github_timeout_seconds: float = 60

    # --- retrieval ---
    retrieval_top_k: int = 6
    min_relevance: float = 0.35  # vector-similarity gate, used only when re-ranking is off
    # Re-ranker gate for Q&A: answer "not found" (without calling the LLM) when the best code chunk's
    # cross-encoder relevance is below this. The cross-encoder was trained on web text and scores code
    # low; on the benchmark every unanswerable question scored <= 0.005. Tuned on that benchmark, so
    # optimistic — see docs/DESIGN.md and docs/known-limitations.md.
    min_rerank_relevance: float = 0.01

    # --- logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _secret_strength(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("ADI_JWT_SECRET must be at least 32 characters")
        return v

    @property
    def llm_mode(self) -> Literal["claude", "offline"]:
        """Resolve ``auto``: Claude when an API key is configured, otherwise offline."""
        if self.llm_provider == "auto":
            return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "offline"
        return self.llm_provider


@lru_cache
def get_settings() -> Settings:
    return Settings()
