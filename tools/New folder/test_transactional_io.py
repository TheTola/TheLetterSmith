from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from transactional_io import (
    PathTransaction,
    atomic_write_text,
    cleanup_abandoned_staging,
    create_staging_directory,
    validate_directory,
)


def test_atomic_write_preserves_original_when_replace_fails(tmp_path: Path) -> None:
    target = tmp_path / "message.html"
    target.write_text("original", encoding="utf-8")

    with mock.patch("transactional_io.os.replace", side_effect=OSError("replace failed")):
        with pytest.raises(OSError, match="replace failed"):
            atomic_write_text(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert not tuple(tmp_path.glob(f".{target.name}.tmp.*"))


def test_directory_transaction_rolls_back_after_later_failure(tmp_path: Path) -> None:
    target = tmp_path / "active"
    target.mkdir()
    (target / "letter.txt").write_text("original", encoding="utf-8")

    transaction = PathTransaction(target)
    staging = transaction.prepare()
    staging.mkdir()
    (staging / "letter.txt").write_text("replacement", encoding="utf-8")
    transaction.commit(keep_backup=True)

    transaction.rollback()

    assert (target / "letter.txt").read_text(encoding="utf-8") == "original"
    assert not transaction.staging_path.exists()
    assert not transaction.backup_path.exists()


def test_staging_validation_and_abandoned_cleanup(tmp_path: Path) -> None:
    staging = create_staging_directory(tmp_path, prefix=".letter-staging-")
    (staging / "index.html").write_text("<html></html>", encoding="utf-8")

    validate_directory(staging, required_paths=("index.html",))
    os.utime(staging, (1, 1))

    removed = cleanup_abandoned_staging(
        tmp_path,
        prefix=".letter-staging-",
        older_than_seconds=1,
        now=10,
    )

    assert removed == (staging,)
    assert not staging.exists()
