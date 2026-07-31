"""Transaction models for planned refactoring operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EditOp(str, Enum):
    """Type of edit operation.

    All text edits apply as a byte-range splice (``content[start:end] = new``),
    so the three text ops are encodings of that one mechanism:

    * ``REPLACE`` — ``start < end``; ``old`` is the replaced text.
    * ``INSERT``  — ``start == end`` (zero-width) and ``old == ""``; ``new`` is
      inserted at ``start``.
    * ``DELETE``  — ``start < end`` and ``new == ""``; the range is removed.

    ``CREATE_FILE`` and ``DELETE_FILE`` are file-level, not byte-range,
    operations — see :class:`FileCreateEntry` / :class:`FileDeleteEntry`.
    """

    REPLACE = "replace"
    INSERT = "insert"
    DELETE = "delete"
    RENAME_FILE = "rename_file"
    CREATE_FILE = "create_file"
    DELETE_FILE = "delete_file"


class TransactionStatus(str, Enum):
    """Lifecycle status of a transaction."""

    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class EditEntry:
    """A single text replacement within a file.

    Uses byte offsets for precision when applying edits.
    """

    file: str
    start: int  # byte offset of first byte to replace
    end: int  # byte offset one past last byte to replace
    old: str  # original text (for verification + rollback)
    new: str  # replacement text
    file_hash: str  # SHA-256 of file at plan time
    op: EditOp = EditOp.REPLACE


@dataclass
class FileRenameEntry:
    """A file rename operation."""

    old_path: str
    new_path: str
    file_hash: str  # SHA-256 of file at plan time
    op: EditOp = EditOp.RENAME_FILE


@dataclass
class FileCreateEntry:
    """A file creation.

    The pre-image of a creation is *absence*, not bytes, so the entry is
    self-contained rather than a splice: ``content`` is the full text to
    write, which is what makes rollback and ``transactions show`` possible
    with no other source of truth. There is deliberately no ``file_hash`` —
    pre-flight verifies that ``path`` does not exist, not a hash of bytes
    that were never there. ``content_hash`` is the SHA-256 of ``content``;
    it is checked only by rollback, to refuse deleting a file someone
    edited after apply (the same parity :class:`EditEntry`'s ``file_hash``
    gives text edits).
    """

    path: str
    content: str
    content_hash: str
    op: EditOp = EditOp.CREATE_FILE


@dataclass
class FileDeleteEntry:
    """A file deletion.

    ``content`` pins the full pre-image so rollback can recreate the file
    byte-for-byte. ``file_hash`` keeps :class:`EditEntry`'s meaning — the
    SHA-256 of the file at plan time — and is verified at pre-flight so a
    deletion refuses if the file changed since planning.
    """

    path: str
    content: str
    file_hash: str  # SHA-256 of file at plan time
    op: EditOp = EditOp.DELETE_FILE


@dataclass
class TransactionHeader:
    """Metadata for a transaction, written as the first line of the JSONL.

    ``version`` is the on-disk format version, additive with a default of
    ``1`` so every transaction already on disk reads back as version 1 with
    no migration. :meth:`~pypeeker.storage.transaction_store.TransactionStore.save`
    writes ``version = 2`` only when the transaction actually contains a
    create or delete entry; a transaction with only text edits and/or a
    rename stays version 1. See storage-transaction-architecture.md.
    """

    tx_id: str
    symbol_id: str
    old_name: str
    new_name: str
    created_at: str  # ISO-8601 timestamp
    operation: str = "rename"
    status: TransactionStatus = TransactionStatus.PENDING
    include_file: bool = False
    include_exports: bool = False
    version: int = 1


@dataclass
class TransactionSummary:
    """Summary returned by plan-rename for JSON output."""

    tx_id: str
    operation: str
    symbol_id: str
    old_name: str
    new_name: str
    edit_count: int
    created_at: str
    files_affected: list[str] = field(default_factory=list)
