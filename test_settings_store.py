from __future__ import annotations

import json
from pathlib import Path

from settings_store import SettingsStore


def test_settings_updates_merge_without_clobbering_other_writers(tmp_path: Path) -> None:
    first = SettingsStore(tmp_path)
    second = SettingsStore(tmp_path)

    first.update_fields(recipient_name="Amanda")
    second.update_fields(recipient_title="A Letter")

    settings = first.as_dict()
    assert settings["recipient_name"] == "Amanda"
    assert settings["recipient_title"] == "A Letter"
    assert not tuple(tmp_path.glob(".settings.json.tmp.*"))


def test_invalid_settings_are_backed_up_and_migrated(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{broken", encoding="utf-8")

    store = SettingsStore(tmp_path)
    migrated = store.update_fields(
        starting_volume=900,
        music_volume="bad",
        last_audio="../music/custom.mp3",
        curtain_style="average color",
    )

    assert migrated["starting_volume"] == 100
    assert migrated["music_volume"] == 100
    assert migrated["last_audio"] == "custom.mp3"
    assert migrated["curtain_style"] == "average_color"
    assert tuple(tmp_path.glob("settings.invalid.*.json"))
    assert json.loads(settings_path.read_text(encoding="utf-8")) == migrated


def test_changed_event_reports_only_updated_fields(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    events: list[tuple[str, ...]] = []
    store.changed.connect(lambda _settings, keys: events.append(keys))

    store.update_fields(recipient_name="Ada", recipient_title="Hello")

    assert events == [("recipient_name", "recipient_title")]


def test_snapshot_returns_an_independent_current_copy(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.update_fields(recipient_name="Ada", unknown_future_field={"kept": True})

    snapshot = store.snapshot()
    snapshot["recipient_name"] = "Changed locally"

    assert store.get("recipient_name") == "Ada"
    assert store.snapshot()["unknown_future_field"] == {"kept": True}
