from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from transactional_io import atomic_write_bytes


_CREDENTIAL_SCHEMA_VERSION = 1
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


@dataclass(frozen=True)
class R2Credentials:
    access_key_id: str
    secret_access_key: str

    def validated(self) -> "R2Credentials":
        access_key_id = str(self.access_key_id).strip()
        secret_access_key = str(self.secret_access_key).strip()
        if not access_key_id or not secret_access_key:
            raise ValueError("Both R2 access keys are required.")
        return R2Credentials(access_key_id, secret_access_key)


class _DataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    )


def _input_blob(value: bytes) -> tuple[_DataBlob, object]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return (
        _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ),
        buffer,
    )


def _protect_for_current_windows_user(value: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Secure R2 credential storage requires Windows.")
    source, source_buffer = _input_blob(value)
    protected = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    result = crypt32.CryptProtectData(
        ctypes.byref(source),
        "Letter Smith R2 credentials",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    )
    del source_buffer
    if not result:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)


def _unprotect_for_current_windows_user(value: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Secure R2 credential storage requires Windows.")
    source, source_buffer = _input_blob(value)
    clear = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    result = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(clear),
    )
    del source_buffer
    if not result:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(clear.pbData, clear.cbData)
    finally:
        kernel32.LocalFree(clear.pbData)


def _default_credential_path() -> Path:
    local_app_data = str(os.environ.get("LOCALAPPDATA", "")).strip()
    if not local_app_data:
        raise OSError("Windows Local AppData is unavailable.")
    return (
        Path(local_app_data)
        / "LetterSmith"
        / "credentials"
        / "cloudflare-r2.dpapi"
    )


class R2CredentialStore:
    """Store R2 keys encrypted for the current Windows account."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.path = Path(path).resolve() if path is not None else _default_credential_path()
        self._protect = protect or _protect_for_current_windows_user
        self._unprotect = unprotect or _unprotect_for_current_windows_user

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, credentials: R2Credentials) -> None:
        valid = credentials.validated()
        payload = {
            "schema_version": _CREDENTIAL_SCHEMA_VERSION,
            **asdict(valid),
        }
        clear = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        atomic_write_bytes(self.path, self._protect(clear))

    def load(self) -> R2Credentials | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
            if not isinstance(payload, dict):
                return None
            if int(payload.get("schema_version", 0)) != _CREDENTIAL_SCHEMA_VERSION:
                return None
            return R2Credentials(
                str(payload.get("access_key_id", "")),
                str(payload.get("secret_access_key", "")),
            ).validated()
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


__all__ = ["R2CredentialStore", "R2Credentials"]
