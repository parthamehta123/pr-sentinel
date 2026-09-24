"""Typed configuration. One source of truth, validated at import time."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # --- GitHub ---
    github_webhook_secret: str = "change-me"  # noqa: S105 - placeholder sentinel, not a secret
    github_token: str = ""
    github_api_url: str = "https://api.github.com"

    # --- Datastore ---
    database_url: str = "postgresql://sentinel:sentinel@localhost:5433/sentinel"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # --- Queue ---
    redis_url: str = "redis://localhost:6380/0"

    # --- LLM ---
    llm_provider: Literal["anthropic", "echo"] = "anthropic"
    anthropic_api_key: str = ""

    model_security: str = "claude-opus-5"
    model_correctness: str = "claude-opus-5"
    model_tests: str = "claude-sonnet-5"
    model_docs: str = "claude-haiku-4-5"

    max_output_tokens: int = 16000

    # --- Embeddings ---
    embedding_provider: Literal["hash", "openai", "local"] = "hash"
    embedding_model: str = "text-embedding-3-small"
    # Used when embedding_provider is "local". 256-dimensional, ~30MB,
    # downloaded once and cached; see LocalEmbedder on padding.
    local_embedding_model: str = "minishlab/potion-base-8M"
    embedding_dim: int = 1536
    openai_api_key: str = ""

    # --- Confidence gate ---
    auto_post_confidence: float = Field(0.70, ge=0.0, le=1.0)
    finding_post_confidence: float = Field(0.60, ge=0.0, le=1.0)
    escalate_critical_security: bool = True
    # Severity floor for posting. Confidence answers "is this real"; severity
    # answers "does it matter". A correct, high-confidence, trivial observation
    # clears every confidence check and is still not worth a reviewer's attention.
    post_min_severity: Literal["info", "minor", "major", "critical"] = "minor"
    # A reviewer who needs to add tests needs one comment naming what is
    # uncovered, not one per function. Above this many repeats of the same
    # recommendation from one agent, they collapse into a single finding.
    collapse_repeated_after: int = 3

    # --- Budget ---
    daily_cost_cap_usd: float = 25.0
    review_cost_cap_usd: float = 1.50

    # --- Timeouts (seconds) ---
    llm_timeout_s: float = 180.0
    agent_timeout_s: float = 240.0
    github_timeout_s: float = 20.0
    db_timeout_s: float = 15.0

    # --- Retrieval ---
    retrieval_top_k: int = 12
    max_diff_bytes: int = 400_000

    @model_validator(mode="after")
    def _check_gate_ordering(self) -> Settings:
        if self.finding_post_confidence > self.auto_post_confidence:
            raise ValueError(
                "FINDING_POST_CONFIDENCE must not exceed AUTO_POST_CONFIDENCE — otherwise a "
                "review can clear the gate with nothing left to say."
            )
        return self

    def model_for(self, agent: str) -> str:
        return {
            "security": self.model_security,
            "correctness": self.model_correctness,
            "tests": self.model_tests,
            "docs": self.model_docs,
        }.get(agent, self.model_correctness)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # pydantic-settings fills from env
