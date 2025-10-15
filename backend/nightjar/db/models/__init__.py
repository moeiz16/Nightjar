"""Import every model module so `Base.metadata` is complete (Alembic, tests). Builder A adds `soc` here."""

from nightjar.db.models import platform  # noqa: F401
