"""Transaction storage.

Manages the ``.pypeeker/transactions/*.jsonl`` files written by
:mod:`pypeeker.refactor.planner` and consumed by
:mod:`pypeeker.refactor.applier`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from pypeeker.models import (
    EditEntry,
    EditOp,
    FileCreateEntry,
    FileDeleteEntry,
    FileRenameEntry,
    TransactionHeader,
    TransactionStatus,
    from_dict,
    from_json,
    to_json,
)
from pypeeker.storage.index_store import resolve_storage_root

TRANSACTIONS_DIR = "transactions"

# The highest TransactionHeader.version this build understands. A header
# version above this refuses to load rather than silently misparsing a
# forward-format transaction.
_SUPPORTED_VERSIONS = frozenset({1, 2})

# Edit-line ops that decode as EditEntry (the byte-range splice family).
_EDIT_OPS = frozenset({EditOp.REPLACE.value, EditOp.INSERT.value, EditOp.DELETE.value})

# The lowest header version that may carry file-lifecycle entries. A header
# at or above it whose create/delete lists are empty means a caller read a
# transaction and is about to write it back having dropped them.
_FILE_LIFECYCLE_VERSION = 2


class TransactionLoadError(Exception):
    """A transaction file could not be loaded: a stable ``code`` plus message.

    Raised by :meth:`TransactionStore.load` when a line's ``op`` or the
    header's ``version`` is one this build does not understand, so a
    forward-format transaction refuses cleanly instead of raising a raw
    ``TypeError`` out of :func:`~pypeeker.models.serialize.from_dict` (which
    would happen if an unrecognised op's line were coerced into the wrong
    entry dataclass and its required fields were missing).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LoadedTransaction:
    """A transaction as read back from disk.

    ``creates``/``deletes`` are ``FileCreateEntry``/``FileDeleteEntry``
    lists (a transaction may create or delete any number of files);
    ``file_rename`` stays singular, at most one per transaction.

    3-element iteration/indexing as ``(header, edits, file_rename)`` is kept
    for callers written against the pre-existing tuple shape (the arity is
    purely internal — no JSON payload depends on it). **New code should use
    attribute access**: a 3-element destructure cannot see ``creates`` or
    ``deletes``, so a read-modify-write built on it would drop them.

    That hazard is not left to convention. Dropping file entries on the way
    back to disk is refused by :meth:`TransactionStore.save`, which will not
    write a file-lifecycle header without being handed both lists
    explicitly — so a caller that destructures three elements and saves the
    result fails loudly instead of silently erasing the create/delete lines
    and downgrading the header to version 1.
    """

    header: TransactionHeader
    edits: list[EditEntry]
    file_rename: FileRenameEntry | None
    creates: list[FileCreateEntry] = field(default_factory=list)
    deletes: list[FileDeleteEntry] = field(default_factory=list)

    def _as_triple(self) -> tuple:
        return (self.header, self.edits, self.file_rename)

    def __iter__(self):
        return iter(self._as_triple())

    def __getitem__(self, index):
        return self._as_triple()[index]


class TransactionStore:
    """Refactor transactions as JSONL under ``.pypeeker/transactions/``."""

    def __init__(self, project_root: Path) -> None:
        self._root = resolve_storage_root(project_root) / TRANSACTIONS_DIR

    @property
    def root(self) -> Path:
        """The ``.pypeeker/transactions/`` directory holding transaction files."""
        return self._root

    def save(
        self,
        header: TransactionHeader,
        edits: list[EditEntry],
        file_rename: FileRenameEntry | None = None,
        creates: list[FileCreateEntry] | None = None,
        deletes: list[FileDeleteEntry] | None = None,
    ) -> Path:
        """Write a transaction as JSONL.

        Line order: header, edits, creates, deletes, then the optional
        rename. ``header.version`` is written as ``2`` when ``creates`` or
        ``deletes`` is non-empty, else ``1`` — additive, so a transaction
        with only text edits and/or a rename is byte-for-byte what a
        pre-file-lifecycle build would have written.

        Raises :class:`ValueError` when ``header`` already carries a
        file-lifecycle version and either list was left unpassed. Such a
        header only comes from a transaction that was read back from disk,
        so an omitted list is a read-modify-write that dropped it — and
        writing that would silently erase the create/delete lines and
        downgrade the header to version 1, leaving a transaction that
        applies without creating and rolls back without removing. Both
        lists must be passed explicitly (``[]`` to clear them on purpose).
        This is what keeps :class:`LoadedTransaction`'s legacy 3-element
        destructure a read-only convenience rather than a data-loss path.
        """
        if header.version >= _FILE_LIFECYCLE_VERSION and (
            creates is None or deletes is None
        ):
            raise ValueError(
                f"Transaction '{header.tx_id}' has version {header.version} "
                "(file-lifecycle) but save() was called without its "
                "creates/deletes; refusing to silently drop them. Pass the "
                "loaded transaction's .creates and .deletes through "
                "explicitly (or [] to clear them deliberately)."
            )
        creates = creates or []
        deletes = deletes or []
        version = _FILE_LIFECYCLE_VERSION if (creates or deletes) else 1
        header_to_write = replace(header, version=version)

        self._root.mkdir(parents=True, exist_ok=True)
        tx_path = self._root / f"{header.tx_id}.jsonl"
        with tx_path.open("w") as f:
            f.write(to_json(header_to_write) + "\n")
            for edit in edits:
                f.write(to_json(edit) + "\n")
            for create in creates:
                f.write(to_json(create) + "\n")
            for delete in deletes:
                f.write(to_json(delete) + "\n")
            if file_rename:
                f.write(to_json(file_rename) + "\n")
        return tx_path

    def load(self, tx_id: str) -> LoadedTransaction | None:
        """Load a transaction, or ``None`` if ``tx_id`` has no file on disk.

        Raises :class:`TransactionLoadError` if the header's version is
        unsupported or any line's ``op`` is not recognised — both are clean,
        machine-readable refusals rather than a raw ``TypeError``.
        """
        tx_path = self._root / f"{tx_id}.jsonl"
        if not tx_path.exists():
            return None
        lines = tx_path.read_text().strip().split("\n")
        if not lines:
            return None
        header = from_json(TransactionHeader, lines[0])
        if header.version not in _SUPPORTED_VERSIONS:
            raise TransactionLoadError(
                "unsupported-transaction-version",
                f"Transaction '{tx_id}' has version {header.version}, which "
                "this build does not understand "
                f"(supported: {sorted(_SUPPORTED_VERSIONS)}).",
            )

        edits: list[EditEntry] = []
        file_rename: FileRenameEntry | None = None
        creates: list[FileCreateEntry] = []
        deletes: list[FileDeleteEntry] = []
        for line in lines[1:]:
            data = json.loads(line)
            op = data.get("op")
            if op in _EDIT_OPS:
                edits.append(from_dict(EditEntry, data))
            elif op == EditOp.RENAME_FILE.value:
                file_rename = from_dict(FileRenameEntry, data)
            elif op == EditOp.CREATE_FILE.value:
                creates.append(from_dict(FileCreateEntry, data))
            elif op == EditOp.DELETE_FILE.value:
                deletes.append(from_dict(FileDeleteEntry, data))
            else:
                raise TransactionLoadError(
                    "unknown-transaction-op",
                    f"Transaction '{tx_id}' has an unrecognised op {op!r}; "
                    "this build cannot load it.",
                )
        return LoadedTransaction(
            header=header,
            edits=edits,
            file_rename=file_rename,
            creates=creates,
            deletes=deletes,
        )

    def update_status(self, tx_id: str, status: TransactionStatus) -> None:
        """Rewrite the header line with a new status, keeping edit/rename lines.

        This is how the transaction lifecycle is persisted: the applier marks
        transactions ``APPLIED`` on success and ``FAILED`` after a rollback,
        and the rollback command marks them ``ROLLED_BACK``. Raises
        :class:`FileNotFoundError` if the transaction does not exist.
        """
        tx_path = self._root / f"{tx_id}.jsonl"
        if not tx_path.exists():
            raise FileNotFoundError(f"Transaction not found: {tx_id}")
        lines = tx_path.read_text().strip().split("\n")
        header = from_json(TransactionHeader, lines[0])
        header.status = status
        lines[0] = to_json(header)
        tx_path.write_text("\n".join(lines) + "\n")

    def remove(self, tx_id: str) -> None:
        """Delete a transaction file."""
        tx_path = self._root / f"{tx_id}.jsonl"
        if tx_path.exists():
            tx_path.unlink()

    def list(self) -> list[str]:
        """List all transaction IDs, regardless of status."""
        if not self._root.exists():
            return []
        return sorted(p.stem for p in self._root.glob("*.jsonl"))
