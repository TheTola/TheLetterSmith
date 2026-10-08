"""Ordered, stable page identities shared by the editors and exported viewer."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path

from transactional_io import atomic_write_json

LAYOUT_FILE = "lettersmith-pages.json"
MIN_PAGES = 2
MAX_PAGES = 10
PAGE_KEYS = ("cover", "letter", "wall", "back", *(f"page_{i}" for i in range(1, 7)))
PAGE_INDICES = {key: index for index, key in enumerate(PAGE_KEYS, 1)}


@dataclass(frozen=True)
class LetterPage:
    key: str
    kind: str

    @property
    def filename(self) -> str:
        return f"{self.key}.png"


DEFAULT_PAGES = (
    LetterPage("cover", "cover"), LetterPage("letter", "letter"),
    LetterPage("wall", "wall"), LetterPage("back", "back"),
)


def normalize_pages(value: object) -> tuple[LetterPage, ...]:
    if value is None:
        return DEFAULT_PAGES
    if not isinstance(value, list) or not MIN_PAGES <= len(value) <= MAX_PAGES:
        raise ValueError("A letter must contain between 2 and 10 pages.")
    pages = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Invalid letter page.")
        key, kind = item.get("key"), item.get("kind")
        if key not in PAGE_KEYS or kind not in {"cover", "letter", "wall", "back"}:
            raise ValueError("Invalid page identity or type.")
        pages.append(LetterPage(key, kind))
    if len({page.key for page in pages}) != len(pages):
        raise ValueError("Page identities must be unique.")
    if pages[0] != DEFAULT_PAGES[0] or pages[-1] != DEFAULT_PAGES[-1]:
        raise ValueError("Cover must be first and End must be last.")
    if any(page.key in {"cover", "back"} or page.kind not in {"letter", "wall"}
           for page in pages[1:-1]):
        raise ValueError("Middle pages must be Letters or Walls.")
    return tuple(pages)


def pages_payload(pages: tuple[LetterPage, ...]) -> list[dict[str, str]]:
    return [{"key": page.key, "kind": page.kind} for page in pages]


def load_pages(directory: str | Path) -> tuple[LetterPage, ...]:
    path = Path(directory) / LAYOUT_FILE
    if not path.exists():
        return DEFAULT_PAGES
    if path.is_symlink():
        raise ValueError("The page layout must be a regular file.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or "pages" not in data:
        raise ValueError("Unsupported letter page layout.")
    return normalize_pages(data["pages"])


def save_pages(directory: str | Path, pages: tuple[LetterPage, ...]) -> None:
    payload = pages_payload(pages)
    normalize_pages(payload)
    atomic_write_json(Path(directory) / LAYOUT_FILE, {"version": 1, "pages": payload})


def page_labels(pages: tuple[LetterPage, ...]) -> dict[str, str]:
    totals = Counter(page.kind for page in pages)
    counts: Counter[str] = Counter()
    labels = {}
    for page in pages:
        counts[page.kind] += 1
        label = "End" if page.kind == "back" else page.kind.title()
        if totals[page.kind] > 1:
            label += f" {counts[page.kind]}"
        labels[page.key] = label
    return labels


def page_files(directory: str | Path) -> tuple[str, ...]:
    return tuple(page.filename for page in load_pages(directory))


def resized_pages(pages: tuple[LetterPage, ...], count: int) -> tuple[LetterPage, ...]:
    count = max(MIN_PAGES, min(MAX_PAGES, count))
    middle = list(pages[1:-1])[:count - 2]
    for key in PAGE_KEYS:
        if len(middle) >= count - 2:
            break
        if key not in {"cover", "back"} and key not in {page.key for page in middle}:
            middle.append(LetterPage(key, "wall" if key == "wall" else "letter"))
    return (DEFAULT_PAGES[0], *middle, DEFAULT_PAGES[-1])
