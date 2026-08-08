"""Cli main, unconstrained."""
from app.db.session import open_session


def go():
    """Go."""
    return open_session()
