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
    provider: str = ""
    published_at: str = ""
    expires_at: str = ""
    source_fingerprint: str = ""
    owner: str = ""
    repository: str = ""
    verified: bool = False
    message: str = ""
    technical_details: str = ""
    error_code: str = ""
