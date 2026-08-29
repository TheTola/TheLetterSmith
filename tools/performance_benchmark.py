from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import generate
from saved_letters import SavedLetterCatalog
from settings_store import ACTIVE_PLAY_DIR_KEY, SettingsStore


def _write_bundle(root: Path, index: int, project_id: str) -> Path:
    recipient = f"Recipient {index:04d}"
    title = f"Letter {index:04d}"
    bundle = root / "output" / "Play" / recipient / title
    pages = bundle / "gallery" / "pages"
    controls = bundle / "gallery" / "controls"
    message = bundle / "gallery" / "message"
    pages.mkdir(parents=True)
    controls.mkdir(parents=True)
    message.mkdir(parents=True)
    for name in config.REQUIRED_SLIDES:
        (pages / name).write_bytes(b"image")
    for name in config.CONTROL_FILES:
        (controls / name).write_bytes(b"control")
    (message / "message.html").write_text("<p>Letter</p>", encoding="utf-8")
    for name in ("index.html", "styles.css", "script.js"):
        (bundle / name).write_text("", encoding="utf-8")
    (bundle / config.PLAY_METADATA_FILE).write_text(
        json.dumps(
            {
                "project_id": project_id,
                "recipient_name": recipient,
                "recipient_title": title,
            }
        ),
        encoding="utf-8",
    )
    return bundle.resolve()


def _milliseconds(function, *, repeat: int = 5) -> float:
    samples = []
    for _index in range(max(1, repeat)):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def benchmark(letter_count: int) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        project_ids = [str(uuid.uuid4()) for _index in range(letter_count)]
        bundles = [
            _write_bundle(root, index, project_ids[index])
            for index in range(letter_count)
        ]
        SettingsStore(root).update_fields(
            {
                "project_id": project_ids[0],
                "recipient_id": str(uuid.uuid4()),
                "recipient_name": "Recipient 0000",
                "recipient_display_name": "Recipient 0000",
                "recipient_normalized_key": "recipient 0000",
                "recipient_title": "Letter 0000",
                ACTIVE_PLAY_DIR_KEY: str(bundles[0]),
            }
        )
        active_ms = _milliseconds(lambda: generate.play_bundle_directory(root))
        catalog = SavedLetterCatalog(root)
        started = time.perf_counter()
        entries = catalog.list_entries(force_refresh=True)
        catalog_first_ms = (time.perf_counter() - started) * 1000.0
        catalog_cached_ms = _milliseconds(catalog.list_entries)
        catalog_reopen_ms = _milliseconds(
            lambda: SavedLetterCatalog(root).list_entries()
        )
        catalog_refresh_ms = _milliseconds(
            lambda: catalog.refresh_entry(bundles[0])
        )
        return {
            "letters": letter_count,
            "active_bundle_ms": round(active_ms, 3),
            "catalog_first_ms": round(catalog_first_ms, 3),
            "catalog_cached_ms": round(catalog_cached_ms, 3),
            "catalog_reopen_ms": round(catalog_reopen_ms, 3),
            "catalog_targeted_refresh_ms": round(catalog_refresh_ms, 3),
            "catalog_entries": len(entries),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--letters",
        nargs="+",
        type=int,
        default=(1, 100, 1000),
    )
    arguments = parser.parse_args()
    results = [benchmark(max(1, count)) for count in arguments.letters]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
