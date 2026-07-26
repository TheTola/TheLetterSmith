from __future__ import annotations

import os
import shutil
from pathlib import Path


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


class PathTransaction:
    """Replace one file or directory through sibling staging and backup paths."""

    def __init__(
        self,
        final_path: str | Path,
        *,
        staging_suffix: str = ".staging",
        backup_suffix: str = ".backup",
    ) -> None:
        self.final_path = Path(final_path).resolve()
        self.staging_path = self.final_path.with_name(self.final_path.name + staging_suffix)
        self.backup_path = self.final_path.with_name(self.final_path.name + backup_suffix)
        self._had_original = False
        self._committed = False
        self._validate_paths()

    def _validate_paths(self) -> None:
        parent = self.final_path.parent
        if not self.final_path.name or parent == self.final_path:
            raise ValueError(f"Unsafe transaction target: {self.final_path}")
        for path in (self.staging_path, self.backup_path):
            if path.parent != parent or path == self.final_path:
                raise ValueError(f"Transaction path escaped target parent: {path}")

    def prepare(self) -> Path:
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        if self.backup_path.exists():
            if self.final_path.exists():
                _remove_path(self.backup_path)
            else:
                os.replace(self.backup_path, self.final_path)
        _remove_path(self.staging_path)
        self._had_original = self.final_path.exists()
        self._committed = False
        return self.staging_path

    def commit(self, *, replace: bool = True, keep_backup: bool = False) -> None:
        if replace and not self.staging_path.exists():
            raise FileNotFoundError(f"Transaction staging path is missing: {self.staging_path}")

        _remove_path(self.backup_path)
        self._had_original = self.final_path.exists()
        if self._had_original:
            os.replace(self.final_path, self.backup_path)

        try:
            if replace:
                os.replace(self.staging_path, self.final_path)
            self._committed = True
        except Exception:
            if self.backup_path.exists() and not self.final_path.exists():
                os.replace(self.backup_path, self.final_path)
            raise

        if not keep_backup:
            self.finalize()

    def rollback(self) -> None:
        if self._committed and self.final_path.exists():
            _remove_path(self.final_path)
        if self.backup_path.exists():
            os.replace(self.backup_path, self.final_path)
        self._committed = False
        _remove_path(self.staging_path)

    def finalize(self) -> None:
        _remove_path(self.backup_path)
        _remove_path(self.staging_path)
        self._committed = False

    def abort(self) -> None:
        if self._committed:
            self.rollback()
        else:
            _remove_path(self.staging_path)
