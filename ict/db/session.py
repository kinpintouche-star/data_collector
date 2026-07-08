from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ict.core.config import get_settings


def build_engine(database_url: str | None = None) -> Engine:
    return create_engine(sqlalchemy_database_url(database_url or get_settings().database_url), pool_pre_ping=True)


def sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    return database_url


def build_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=build_engine(database_url), expire_on_commit=False)


@lru_cache(maxsize=4)
def get_sessionmaker(database_url: str | None = None) -> sessionmaker[Session]:
    return build_sessionmaker(database_url)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
