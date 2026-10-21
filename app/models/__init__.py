"""SQLAlchemy models.

Every model must be imported here so Alembic's autogenerate can see it.
The schema is designed in Week 1 (see DESIGN.md §6).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
