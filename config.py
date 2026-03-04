"""
Bot configuration — loaded from environment variables.
All sensitive values come from env vars, never hardcoded.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class Config:
    # ── Discord ───────────────────────────────────────────────────────────────
    discord_token: str = field(
        default_factory=lambda: os.getenv("DISCORD_TOKEN", "")
    )
    guild_id: int | None = field(
        default_factory=lambda: int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else None
    )

    # ── Bot identity ──────────────────────────────────────────────────────────
    bot_name: str = field(
        default_factory=lambda: os.getenv("BOT_NAME", "Team Manager")
    )
    bot_description: str = field(
        default_factory=lambda: os.getenv(
            "BOT_DESCRIPTION",
            "Esports team management — roster, availability, calendar & stats"
        )
    )

    # ── Database ──────────────────────────────────────────────────────────────
    # Railway injects DATABASE_URL automatically when you add a PostgreSQL service.
    # Locally, falls back to SQLite for easy development.
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./data/manager.db"
        )
    )

    # ── Henrik Dev Valorant API ───────────────────────────────────────────────
    henrik_api_key: str = field(
        default_factory=lambda: os.getenv("HENRIK_API_KEY", "")
    )

    # ── pracc.com sync (optional) ─────────────────────────────────────────────
    pracc_email: str = field(default_factory=lambda: os.getenv("PRACC_EMAIL", ""))
    pracc_password: str = field(default_factory=lambda: os.getenv("PRACC_PASSWORD", ""))
    pracc_sync_enabled: bool = field(
        default_factory=lambda: os.getenv("PRACC_SYNC_ENABLED", "false").lower() == "true"
    )

    # ── Misc ──────────────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    def validate(self) -> None:
        """Raise ValueError if any required config is missing."""
        errors = []
        if not self.discord_token:
            errors.append("DISCORD_TOKEN is not set")
        if errors:
            raise ValueError("Missing required environment variables:\n  - " + "\n  - ".join(errors))

    @property
    def is_postgres(self) -> bool:
        return "postgresql" in self.database_url or self.database_url.startswith("postgres://")

    @property
    def is_henrik_configured(self) -> bool:
        return bool(self.henrik_api_key)

    @property
    def is_pracc_configured(self) -> bool:
        return bool(self.pracc_email and self.pracc_password and self.pracc_sync_enabled)


config = Config()
