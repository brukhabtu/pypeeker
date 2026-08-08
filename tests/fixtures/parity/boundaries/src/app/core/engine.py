"""Core engine."""
from app.db.session import open_session


def run():
    """Run it."""
    return open_session()
