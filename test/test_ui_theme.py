from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtGui, QtWidgets

from image_button import ArtworkButton
from settings_store import SettingsStore
from ui_theme import (
    DEFAULT_THEME_ID,
    FUTURISTIC_THEME,
    SOFT_ELEGANT_THEME,
    THEMES,
    THEME_SETTINGS_KEY,
    ThemeDefinition,
    ThemeService,
    normalize_theme_id,
)


class ThemeServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix=".theme-test-",
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_complete_immutable_themes_are_available(self) -> None:
        self.assertEqual(
            {theme.theme_id for theme in THEMES.values()},
            {"futuristic", "soft_elegant"},
        )
        expected_tokens = {
            "primary",
            "secondary",
            "accent",
            "background",
            "panel_background",
            "card_background",
            "control_background",
            "border",
            "text",
            "muted_text",
            "highlight",
            "hover",
            "active",
            "success",
            "warning",
            "error",
        }
        for theme in THEMES.values():
            self.assertEqual(
                {token.name for token in fields(theme.tokens)},
                expected_tokens,
            )
            for token in fields(theme.tokens):
                self.assertTrue(
                    QtGui.QColor(getattr(theme.tokens, token.name)).isValid()
                )
        with self.assertRaises(FrozenInstanceError):
            FUTURISTIC_THEME.tokens.accent = "#ffffff"  # type: ignore[misc]

    def test_invalid_persisted_theme_normalizes_without_losing_unknown_settings(self) -> None:
        settings = SettingsStore(self.root)
        settings.update_fields(
            {
                THEME_SETTINGS_KEY: "does-not-exist",
                "future_setting": {"keep": True},
            }
        )

        service = ThemeService(self.root, settings=settings)

        self.assertEqual(service.theme_id, DEFAULT_THEME_ID)
        snapshot = settings.snapshot()
        self.assertEqual(snapshot[THEME_SETTINGS_KEY], DEFAULT_THEME_ID)
        self.assertEqual(snapshot["future_setting"], {"keep": True})
        self.assertEqual(normalize_theme_id("UNKNOWN"), DEFAULT_THEME_ID)

    def test_selection_persists_and_emits_once_per_actual_change(self) -> None:
        settings = SettingsStore(self.root)
        service = ThemeService(self.root, settings=settings)
        changes: list[tuple[str, ThemeDefinition]] = []
        service.theme_changed.connect(
            lambda theme_id, definition: changes.append((theme_id, definition))
        )

        selected = service.set_theme("SOFT_ELEGANT")
        service.set_theme("soft_elegant")
        snapshot = service.save()

        self.assertIs(selected, SOFT_ELEGANT_THEME)
        self.assertEqual(snapshot[THEME_SETTINGS_KEY], "soft_elegant")
        self.assertEqual(settings.get(THEME_SETTINGS_KEY), "soft_elegant")
        self.assertEqual(changes, [("soft_elegant", SOFT_ELEGANT_THEME)])

    def test_registered_widgets_refresh_together(self) -> None:
        service = ThemeService(self.root)
        first = QtWidgets.QWidget()
        second = QtWidgets.QWidget()
        self.addCleanup(first.deleteLater)
        self.addCleanup(second.deleteLater)

        service.apply(first)
        service.apply(second, additional_qss="QLabel#Extra { padding: 3px; }")
        old_accent = service.tokens.accent
        self.assertIn(old_accent, first.styleSheet())
        self.assertIn(old_accent, second.styleSheet())

        service.set_theme("soft_elegant", persist=False)

        self.assertEqual(first.property("letterSmithTheme"), "soft_elegant")
        self.assertEqual(second.property("letterSmithTheme"), "soft_elegant")
        self.assertIn(SOFT_ELEGANT_THEME.tokens.accent, first.styleSheet())
        self.assertIn(SOFT_ELEGANT_THEME.tokens.accent, second.styleSheet())
        self.assertIn("QLabel#Extra", second.styleSheet())

    def test_themed_asset_override_wins_and_missing_asset_falls_back(self) -> None:
        themed = (
            self.root
            / "gallery/app/themes/soft_elegant/prompt_writer/Pwrite.png"
        )
        themed.parent.mkdir(parents=True)
        themed.write_bytes(b"themed")
        fallback = self.root / "gallery/app/icons/Pwrite.png"
        fallback.parent.mkdir(parents=True)
        fallback.write_bytes(b"fallback")

        service = ThemeService(self.root)
        service.set_theme("soft_elegant", persist=False)
        resolved = service.resolve_asset(
            "prompt_writer/Pwrite.png",
            fallback="gallery/app/icons/Pwrite.png",
        )
        self.assertEqual(resolved, themed.resolve())

        themed.unlink()
        self.assertEqual(
            service.resolve_asset(
                "prompt_writer/Pwrite.png",
                fallback="gallery/app/icons/Pwrite.png",
            ),
            fallback.resolve(),
        )

    def test_asset_mapping_and_traversal_rejection(self) -> None:
        mapped_theme = ThemeDefinition(
            theme_id="mapped",
            display_name="Mapped",
            tokens=FUTURISTIC_THEME.tokens,
            asset_overrides={"help/idle.gif": "decorations/help.gif"},
        )
        themed = self.root / "gallery/app/themes/mapped/decorations/help.gif"
        themed.parent.mkdir(parents=True)
        themed.write_bytes(b"gif")
        service = ThemeService(
            self.root,
            themes={
                DEFAULT_THEME_ID: FUTURISTIC_THEME,
                "mapped": mapped_theme,
            },
        )
        service.set_theme("mapped", persist=False)
        self.assertEqual(
            service.resolve_asset("help/idle.gif"),
            themed.resolve(),
        )

        for unsafe in ("../secret.png", "/absolute.png", "C:/secret.png"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    service.resolve_asset(unsafe)
        with self.assertRaises(ValueError):
            service.resolve_asset(
                "help/idle.gif",
                fallback="../../secret.gif",
            )
        with self.assertRaises(ValueError):
            ThemeDefinition(
                theme_id="unsafe",
                display_name="Unsafe",
                tokens=FUTURISTIC_THEME.tokens,
                asset_overrides={"help/idle.gif": "../outside.gif"},
            )

    def test_qss_builder_uses_semantic_status_and_control_tokens(self) -> None:
        service = ThemeService(self.root)
        qss = service.build_stylesheet()
        for value in (
            service.tokens.background,
            service.tokens.panel_background,
            service.tokens.control_background,
            service.tokens.accent,
            service.tokens.success,
            service.tokens.warning,
            service.tokens.error,
        ):
            self.assertIn(value, qss)
        self.assertIn('themeRole="accentButton"', qss)

    def test_artwork_button_reloads_theme_specific_cloud_asset(self) -> None:
        fallback = self.root / "gallery/app/icons/buttons/BButton.png"
        themed = (
            self.root
            / "gallery/app/themes/soft_elegant/buttons/BButton.png"
        )
        fallback.parent.mkdir(parents=True)
        themed.parent.mkdir(parents=True)
        self.assertTrue(QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32).save(str(fallback)))
        self.assertTrue(QtGui.QImage(12, 12, QtGui.QImage.Format_ARGB32).save(str(themed)))

        service = ThemeService(self.root)
        button = ArtworkButton("Reset Images", self.root, "BButton.png")
        self.addCleanup(button.deleteLater)
        button.apply_theme_assets(service)
        self.assertEqual(button.artwork_path, fallback.resolve())

        service.set_theme("soft_elegant", persist=False)
        button.apply_theme_assets(service)
        self.assertEqual(button.artwork_path, themed.resolve())


if __name__ == "__main__":
    unittest.main()
