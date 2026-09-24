"""Compatibility settings for the copied masking algorithm."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    veil_llm_review: bool = False
    veil_llm_review_timeout_seconds: int = 60


SETTINGS = Settings()
