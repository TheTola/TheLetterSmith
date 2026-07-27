from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PublishConfiguration:
    configured: bool
    repository: str = ""
    workspace: Path | None = None
    message: str = ""


@dataclass(frozen=True)
class PublishResult:
    success: bool
    url: str = ""
    public_path: str = ""
    message: str = ""
    technical_details: str = ""
