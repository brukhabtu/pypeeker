"""Cases."""


def via_call_result(thing):
    """Receiver is a call result."""
    thing.get().write("a")
    return None


def via_subscript(items):
    """Receiver is a subscript."""
    items[0].write("a")
    return None


def via_param(handle):
    """Receiver is a parameter."""
    handle.write("a")
    return None


def via_self_attr(self):
    """Receiver is an attribute of a param."""
    self.stream.write("a")
    return None
