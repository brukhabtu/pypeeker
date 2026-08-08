"""Core barrel that re-exports a db symbol (laundering)."""
from app.db.session import open_session

__all__ = ["open_session"]
