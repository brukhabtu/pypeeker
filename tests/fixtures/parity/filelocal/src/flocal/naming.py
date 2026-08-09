"""Naming violations for `naming-conventions`: both message shapes, all four labels.

The rule's message has two shapes — with and without the trailing
"— suggested name: '...'" — and its label slot has four ("snake_case",
"PascalCase", "snake_case (or UPPER_SNAKE_CASE)", and "pattern '<value>'" for a
kind the `conventions` option overrides). Both shapes and all four labels are
present below, so a port that drifts by one character on either is caught here
rather than in a consumer project.

The corpus pyproject selects `kinds = ["function", "method", "class",
"variable"]` and overrides the `method` pattern, which is what puts the
"pattern '...'" label on `doThing` while leaving the other three kinds on their
defaults.
"""


def getValue():
    """camelCase function: the snake_case label, with a suggestion."""
    return 1


def _9lives():
    """Stripped to "9lives", which the converter returns unchanged.

    So `suggestion == stripped`, the frozen rule appends nothing, and the
    message ends at "naming convention". Leading underscores are stripped
    before matching, so the finding is about "9lives", not about "_9lives".
    """
    return 2


def legacyAllowed():
    """Matched by the `allow` option's bare-name pattern, so never flagged."""
    return 3


def ____():
    """Underscore-only after stripping, so tolerated — and NOT by the dunder clause.

    The frozen `_is_dunder` requires `len(name) > 4` and this name is exactly
    four characters, so the frozen rule calls it "not a dunder" and falls
    through to the next test, where `"____".lstrip("_")` is empty and the
    symbol is skipped. The port's `not_(row.name.matches("__*__"))` calls it a
    dunder and skips one clause earlier. Same outcome, different route — which
    is only true while the `stripped` clause sits where it does.
    """
    return 4


class bad_class:
    """A snake_case class: the PascalCase label, with a suggestion."""

    def doThing(self):
        """camelCase method: the `conventions` override's "pattern '...'" label."""
        return 5

    def __repr__(self):
        """A dunder: protocol surface, never flagged."""
        return "bad_class()"


class _9Lives:
    """Stripped to "9Lives", which the PascalCase converter returns unchanged.

    The PascalCase label with no suggestion — the pairing the snake_case case
    above cannot show.
    """


badVar = 6
"""Module-level camelCase variable: the "snake_case (or UPPER_SNAKE_CASE)" label."""

GOOD_CONSTANT = 7
"""UPPER_SNAKE is tolerated by the variable convention, so this is a control."""
