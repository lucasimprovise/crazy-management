"""
Async database engine and session management.

Supports PostgreSQL (production/Railway) and SQLite (local dev).
The DATABASE_URL env var controls which one is used:
  - postgresql+asyncpg://...  → PostgreSQL via asyncpg
  - sqlite+aiosqlite:///...   → SQLite (fallback for local dev)
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession, AsyncEngine,
    async_sessionmaker, create_async_engine,
)

from .models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def _build_engine(db_url: str) -> AsyncEngine:
    """
    Create the appropriate async engine.
    PostgreSQL gets a connection pool; SQLite gets the check_same_thread workaround.
    Railway injects DATABASE_URL as postgres:// — we normalize to postgresql+asyncpg://.
    """
    # Railway provides postgres:// — SQLAlchemy needs postgresql+asyncpg://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    # Also handle postgresql:// without driver specified
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    is_postgres = "postgresql" in db_url

    kwargs: dict = {
        "echo": False,
    }

    if is_postgres:
        # Connection pool tuned for a single-instance Discord bot
        kwargs.update({
            "pool_size": 5,
            "max_overflow": 10,
            "pool_pre_ping": True,      # Detect stale connections
            "pool_recycle": 300,        # Recycle connections every 5 min
        })
        logger.info("Database: PostgreSQL")
    else:
        # SQLite — local dev only
        kwargs["connect_args"] = {"check_same_thread": False}
        logger.info("Database: SQLite (local dev)")

    return create_async_engine(db_url, **kwargs)


async def init_db(db_url: str) -> None:
    """Initialize the database engine and create all tables."""
    global _engine, _session_factory

    _engine = _build_engine(db_url)
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables initialized.")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session with automatic rollback on error."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    """Dispose the database engine gracefully."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("Database connection closed.")
