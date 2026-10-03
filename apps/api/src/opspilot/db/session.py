"""Engine and session management.

Synchronous SQLAlchemy 2.0 throughout (ADR 0009). FastAPI runs ``def`` handlers in a
threadpool, Celery tasks are synchronous already, and the whole system shares one
session idiom instead of two.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from opspilot.core.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        s = settings or get_settings()
        _engine = create_engine(
            s.database_url,
            pool_size=s.database_pool_size,
            max_overflow=s.database_max_overflow,
            # A VPS restarts Postgres sometimes; fail on checkout, not mid-query.
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,  # response serialization happens after commit
            autoflush=False,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional unit of work.

    Services use this. One HTTP request or one Celery task equals one scope, so the
    state change and its outbox event cannot end up in different transactions.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        # Only roll back if there is something to roll back. An ``after_commit`` hook
        # that raises leaves the session already committed, and calling rollback there
        # raises InvalidRequestError *over the top of* the real error — which is how a
        # broker outage ends up reported as a SQLAlchemy state bug.
        if session.in_transaction():
            session.rollback()
        raise
    finally:
        session.close()


def db_session() -> Iterator[Session]:
    """FastAPI dependency. Commits on a clean return, rolls back on any exception.

    Register it only through ``auth.dependencies`` (function scope), so the commit
    completes before the client is told the request succeeded.
    """
    with session_scope() as session:
        yield session
