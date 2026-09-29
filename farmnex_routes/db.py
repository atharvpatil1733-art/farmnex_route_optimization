"""Database engine and session for the route optimizer.

The package keeps its own SQLAlchemy Base, so `init_db()` only ever creates the
`rt_*` tables. It never drops or alters anything that already exists in the
main FarmNex database.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_tables_ready = False


def _normalize_url(url: str) -> str:
    # Supabase gives postgres:// or postgresql:// ; SQLAlchemy needs the psycopg (v3) driver name.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = _normalize_url(settings.database_url)
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        elif url.startswith("postgresql"):
            # Supabase's transaction pooler (port 6543) breaks prepared statements.
            kwargs["connect_args"] = {"prepare_threshold": None}
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    """Create the rt_* tables if they don't exist. Safe to call many times."""
    global _tables_ready
    from . import models  # noqa: F401  (registers the tables on Base)

    Base.metadata.create_all(get_engine(), checkfirst=True)
    _tables_ready = True


def get_session() -> Iterator[Session]:
    """FastAPI dependency. The main app can override it with its own session."""
    get_engine()
    if not _tables_ready and settings.auto_create_tables:
        init_db()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """For calling the helper functions from the main backend's own code:

        with session_scope() as s:
            create_delivery_for_order(s, order_id=..., ...)
    """
    yield from get_session()
