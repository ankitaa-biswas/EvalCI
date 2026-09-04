# evalci/db/database.py
# Purpose: PostgreSQL async connection setup using SQLAlchemy + asyncpg.
# Provides the engine, session factory, and FastAPI dependency for injecting
# database sessions into route handlers and Celery tasks.

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://evalci:evalci@localhost:5432/evalci",
)


class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base class.

    All ORM model classes in ``db/models.py`` inherit from this base.
    ``Base.metadata.create_all()`` is called on startup to initialise tables.
    """

    pass


# Async engine — uses asyncpg driver for non-blocking DB I/O
engine = create_async_engine(
    DATABASE_URL,
    echo=bool(os.getenv("SQL_ECHO", False)),
    pool_size=10,
    max_overflow=20,
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
