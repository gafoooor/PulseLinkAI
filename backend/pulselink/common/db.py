"""Database seam: the SQLAlchemy declarative ``Base`` and engine helpers.

The ORM table metadata (``pulselink.common.models``) hangs off this ``Base``.
Alembic's ``env.py`` imports ``Base.metadata`` as its autogenerate target, and
``DATABASE_URL`` is read from the same config seam the services use
(``pulselink.common.config``).

Local PostgreSQL (+PostGIS) backs the demo; this maps to RDS ``db.t3.micro``
(PostgreSQL + PostGIS) in the budget production profile.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from pulselink.common.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all PulseLink ORM models."""


def get_engine(echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine from the configured ``DATABASE_URL``.

    Uses the ``psycopg`` (v3) driver. No connection is opened until the engine
    is first used, so importing this module never requires a running database.
    """

    settings = get_settings()
    url = settings.database_url
    # Normalise the bare ``postgresql://`` URL to the psycopg v3 driver.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, echo=echo, future=True)
