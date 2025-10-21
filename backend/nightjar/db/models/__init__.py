"""Import every model module so `Base.metadata` is complete (Alembic, tests)."""

from importlib.util import find_spec

from nightjar.db.models import platform, soc  # noqa: F401

if find_spec("nightjar.db.models.pipeline") is not None:  # builder B's `pl_` tables
    from nightjar.db.models import pipeline  # noqa: F401
