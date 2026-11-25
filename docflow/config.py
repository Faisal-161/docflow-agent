"""Settings from environment variables (prefix DOCFLOW_)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    db_path: str = "docflow.db"
    erp_seed_dir: str = "data/erp"
    # "rules" runs offline and deterministic; "claude" uses the Anthropic API for extraction + planning
    mode: str = "rules"
    claude_model: str = "claude-sonnet-5-5"
    # Invoices above this need a finance manager's approval instead of an AP clerk's
    manager_threshold: float = 5000.0
    # Money comparisons allow this much rounding difference
    amount_tolerance: float = 0.02
    # Fields below this extraction confidence trigger a request for information
    min_confidence: float = 0.6
    max_agent_steps: int = 10

    @classmethod
    def from_env(cls) -> Settings:
        s = cls()
        for name, value in vars(cls()).items():
            raw = os.environ.get(f"DOCFLOW_{name.upper()}")
            if raw is not None:
                setattr(s, name, type(value)(raw))
        return s


def get_settings() -> Settings:
    return Settings.from_env()
