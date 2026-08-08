"""Extra."""


def logs(msg):
    """Impure only because 'log' is in extra-impure."""
    log(msg)
    return None
