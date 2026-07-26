from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from Forge_Tab import ForgeTab
from readiness import evaluate_readiness
from saved_letters import SavedLetterCatalog
from settings_store import SettingsStore


def _app() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _populate_required(root: Path) -> None:
    pages = root / "gallery/user/pages"
    message = root / "gallery/user/message"
    pages.mkdir(parents=True)
    message.mkdir(parents=True)
    for name in ("cover.png", "letter.png", "wall.png", "back.png"):
        (pages / name).write_bytes(b"image")
    (message / "message.html").write_text("<p>Message</p>", encoding="utf-8")
    SettingsStore(root).update_fields(recipient_name="Ada", recipient_title="Birthday")


def test_readiness_percentage_status_and_missing_only(tmp_path: Path) -> None:
    _populate_required(tmp_path)
    result = evaluate_readiness(tmp_path)

    assert result.completion_percentage == 88
    assert result.status == "Ready — Missing Music"
    assert [item.key for item in result.missing_items] == ["music"]
    assert result.can_preview

    (tmp_path / "gallery/user/pages/back.png").unlink()
    result = evaluate_readiness(tmp_path)
    assert result.completion_percentage == 75
    assert result.status == "Not Ready"
    assert {item.key for item in result.missing_items} == {"back", "music"}
    assert not result.can_preview


def test_saved_letter_catalog_search_sort_thumbnail_and_recovery(tmp_path: Path) -> None:
    older = tmp_path / "output/Play/ada/older"
    newer = tmp_path / "output/Play/bee/newer"
    recovery = tmp_path / "output/Recovery/20260726-010203"
    for path in (older, newer, recovery):
        (path / "gallery/pages").mkdir(parents=True)
        (path / "index.html").write_text("<html><title>Legacy Title</title></html>", encoding="utf-8")
        (path / "gallery/pages/cover.png").write_bytes(b"cover")
    (newer / "play_metadata.json").write_text(
        json.dumps(
            {
                "recipient_name": "Bea",
                "recipient_title": "Newest",
                "published_page_url": "https://example.test/letter",
            }
        ),
        encoding="utf-8",
    )
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    catalog = SavedLetterCatalog(tmp_path)
    entries = catalog.list_entries()

    assert entries[0].path == newer.resolve()
    assert entries[0].published
    assert entries[0].cover_path.name == "cover.png"
    assert catalog.search("bea") == (entries[0],)
    assert any(entry.recovery for entry in entries)
    legacy = next(entry for entry in entries if entry.path == older.resolve())
    assert legacy.title == "Legacy Title"


def test_forge_uses_missing_only_rows_and_renamed_actions(tmp_path: Path) -> None:
    _app()
    _populate_required(tmp_path)
    tab = ForgeTab(tmp_path)
    tab.refresh_readiness()

    assert tab.preview_btn.text() == "Preview Letter"
    assert tab.publish_btn.text() == "Publish Letter"
    assert tab.open_published_btn.text() == "Open Published Letter"
    assert tab.preview_btn.isEnabled()
    visible = {
        key
        for key, button in tab._missing_buttons.items()
        if button.isVisibleTo(tab.readiness_panel)
    }
    assert visible == {"music"}
    assert not hasattr(tab, "_build_load_menu")


def test_missing_item_navigation_signal(tmp_path: Path) -> None:
    _app()
    tab = ForgeTab(tmp_path)
    received: list[str] = []
    tab.fix_requested.connect(received.append)

    tab._missing_buttons["cover"].click()

    assert received == ["cover"]
