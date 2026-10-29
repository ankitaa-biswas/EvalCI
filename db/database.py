# evalci/db/database.py
# Purpose: PostgreSQL async connection setup using SQLAlchemy + asyncpg.
# Provides the engine, session factory, and FastAPI dependency for injecting
# database sessions into route handlers and Celery tasks.

import os
from typing import AsyncGenerator

from sqlalchemy.pool import NullPool

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://evalci:evalci@localhost:5432/evalci",
)


from db.models import Base


# Async engine — uses asyncpg driver for non-blocking DB I/O
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    **({"poolclass": NullPool} if os.getenv("DB_POOL_MODE") == "null" else {"pool_size": 10, "max_overflow": 20}),
)


# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """
    Create all tables defined in ``db/models.py`` if they do not yet exist.

    Called during FastAPI application startup (lifespan hook in ``api/main.py``).
    In production, Alembic migrations should be used instead of
    ``create_all()``; this is provided for development convenience.
    """
    # Import models here to ensure they are registered on Base.metadata
    import db.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    Dispose the SQLAlchemy connection pool gracefully.

    Called during FastAPI application shutdown (lifespan hook in
    ``api/main.py``).
    """
    await engine.dispose()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an ``AsyncSession`` per request.

    Usage in route handlers::

        @router.get("/example")
        async def my_handler(db: AsyncSession = Depends(get_db)):
            ...

    The session is automatically closed after the response is returned.

    Yields:
        AsyncSession: A database session for the duration of the request.
    """
    async with AsyncSessionLocal() as session:
        yield session
