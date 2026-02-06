"""Transaction models for planned refactoring operations."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class EditOp(str, Enum):
    """Type of edit operation."""

    REPLACE = "replace"
    RENAME_FILE = "rename_file"


class TransactionStatus(str, Enum):
    """Lifecycle status of a transaction."""

    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class EditEntry(BaseModel):
    """A single text replacement within a file.

    Uses byte offsets for precision when applying edits.
    """

    op: EditOp = EditOp.REPLACE
    file: str
    start: int  # byte offset of first byte to replace
    end: int  # byte offset one past last byte to replace
    old: str  # original text (for verification + rollback)
    new: str  # replacement text
    file_hash: str  # SHA-256 of file at plan time


class FileRenameEntry(BaseModel):
    """A file rename operation."""

    op: EditOp = EditOp.RENAME_FILE
    old_path: str
    new_path: str
    file_hash: str  # SHA-256 of file at plan time


class TransactionHeader(BaseModel):
    """Metadata for a transaction, written as the first line of the JSONL."""

    tx_id: str
    operation: str = "rename"
    symbol_id: str
    old_name: str
    new_name: str
    created_at: str  # ISO-8601 timestamp
    status: TransactionStatus = TransactionStatus.PENDING
    include_file: bool = False
    include_exports: bool = False


class TransactionSummary(BaseModel):
    """Summary returned by plan-rename for JSON output."""

    tx_id: str
    operation: str
    symbol_id: str
    old_name: str
    new_name: str
    files_affected: list[str]
    edit_count: int
    created_at: str
