"""Api handler."""
import importlib

from app.core import open_session as laundered
from app.core.engine import run
from app.db.session import open_session

_dyn = importlib.import_module("app.db.session")


def handle():
    """Handle."""
    return run(), open_session(), laundered(), _dyn
