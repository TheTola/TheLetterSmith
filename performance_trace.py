from __future__ import annotations

import functools
import logging
import os
import threading
from time import perf_counter
from typing import Callable, TypeVar


_LOGGER = logging.getLogger("lettersmith.performance")
_RESULT = TypeVar("_RESULT")


def performance_enabled() -> bool:
    return os.environ.get("LETTERSMITH_PERF", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def log_performance(name: str, elapsed_ms: float, **metrics: object) -> None:
    if not performance_enabled():
        return
    fields = " ".join(
        f"{key}={value}"
        for key, value in sorted(metrics.items())
        if value is not None
    )
    _LOGGER.info(
        "PERF name=%s elapsed_ms=%.3f thread=%s main_thread=%s%s",
        str(name),
        float(elapsed_ms),
        threading.current_thread().name,
        threading.current_thread() is threading.main_thread(),
        f" {fields}" if fields else "",
    )


class PerformanceTimer:
    def __init__(self, name: str) -> None:
        self.name = str(name)
        self.started = perf_counter() if performance_enabled() else 0.0

    def finish(self, **metrics: object) -> float:
        if not self.started:
            return 0.0
        elapsed_ms = (perf_counter() - self.started) * 1000.0
        log_performance(self.name, elapsed_ms, **metrics)
        self.started = 0.0
        return elapsed_ms


def performance_timed(name: str) -> Callable[
    [Callable[..., _RESULT]],
    Callable[..., _RESULT],
]:
    def decorate(function: Callable[..., _RESULT]) -> Callable[..., _RESULT]:
        @functools.wraps(function)
        def wrapped(*args: object, **kwargs: object) -> _RESULT:
            if not performance_enabled():
                return function(*args, **kwargs)
            started = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                log_performance(
                    name,
                    (perf_counter() - started) * 1000.0,
                )

        return wrapped

    return decorate


__all__ = [
    "PerformanceTimer",
    "log_performance",
    "performance_enabled",
    "performance_timed",
]
