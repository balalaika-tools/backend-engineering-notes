"""CTC database engine; per-transaction safeguards live in the query executor."""

from pydantic import SecretStr
from sqlalchemy import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def authenticated_database_url(database_url: str, password: SecretStr) -> URL:
    return make_url(database_url).set(password=password.get_secret_value())


def build_engine(
    *,
    database_url: URL,
    pool_size: int,
    acquisition_timeout_seconds: float = 30,
) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=acquisition_timeout_seconds,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
