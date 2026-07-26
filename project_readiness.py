from __future__ import annotations

from pathlib import Path

from readiness import ReadinessItem, evaluate_readiness


def assess_project_readiness(project_root: str | Path) -> tuple[ReadinessItem, ...]:
    return evaluate_readiness(project_root).items


def project_is_ready(items: tuple[ReadinessItem, ...]) -> bool:
    return all(item.ready for item in items if item.required)


__all__ = ["ReadinessItem", "assess_project_readiness", "project_is_ready"]
