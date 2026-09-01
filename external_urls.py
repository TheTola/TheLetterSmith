"""Cross-platform opening of trusted external web addresses."""

from __future__ import annotations

import sys

from PySide6 import QtCore, QtGui


def open_external_url(value: str | QtCore.QUrl) -> bool:
    """Open one HTTP(S) URL, with a LaunchServices fallback on macOS."""
    url = QtCore.QUrl(value) if isinstance(value, str) else QtCore.QUrl(value)
    if (
        not url.isValid()
        or url.scheme().casefold() not in {"http", "https"}
        or not url.host()
    ):
        return False
    if QtGui.QDesktopServices.openUrl(url):
        return True
    if sys.platform != "darwin":
        return False
    result = QtCore.QProcess.startDetached(
        "/usr/bin/open",
        [url.toString()],
    )
    return result[0] if isinstance(result, tuple) else bool(result)
