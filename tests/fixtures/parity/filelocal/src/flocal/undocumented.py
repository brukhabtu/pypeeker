"""Undocumented public surface: material for `require-docstrings`.

Every public function/class/method below that lacks a docstring is a finding
for the default option set (kinds = function, method, class; visibility =
public). The documented and the private members are negative controls: a port
that ignores the docstring field, or one that ignores visibility, over-fires
on them.
"""


def undocumented_function(value):
    return value + 1


class UndocumentedClass:
    def undocumented_method(self):
        return 0

    def _private_method(self):
        """A private method — documented, and out of the visibility set anyway."""
        return 1


class DocumentedClass:
    """A documented public class: a negative control."""

    def documented_method(self):
        """A documented public method: a negative control."""
        return 2


def documented_function(value):
    """A documented public function: a negative control."""
    return value - 1


def _private_function(value):
    return value * 2
