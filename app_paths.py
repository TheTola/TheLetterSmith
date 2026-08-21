from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Letter Smith"
APP_VERSION = "1.0.0"
ORG_NAME = "Infini Works"
ORG_DOMAIN = "infini.works"


def resolve_application_root(explicit_root: str | Path | None = None) -> Path:
    """Return the one authoritative root for application data and resources."""
    if explicit_root is not None:
        return Path(explicit_root).expanduser().resolve()
    override = os.environ.get("LETTERSMITH_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if bool(getattr(sys, "frozen", False)):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_user_data_root(explicit_root: str | Path | None = None) -> Path:
    """Return the canonical writable data root without changing current layout."""
    return resolve_application_root(explicit_root)


__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "ORG_DOMAIN",
    "ORG_NAME",
    "resolve_application_root",
    "resolve_user_data_root",
]
