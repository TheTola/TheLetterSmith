from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from publishing.models import PublishConfiguration, PublishResult


class Publisher(ABC):
    @abstractmethod
    def is_configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def configure(self, parent=None) -> PublishConfiguration:
        raise NotImplementedError

    @abstractmethod
    def publish(self, build_dir: Path, metadata: dict) -> PublishResult:
        raise NotImplementedError
