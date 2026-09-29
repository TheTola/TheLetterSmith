from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from button_artwork import (
    application_resource_names,
    discover_button_artwork_files,
    session_button_artwork_path,
)
from config import FORCE_THEME_FAMILY_PROMPT
from image_button import ArtworkButton, ButtonSemanticState
from settings_store import SettingsStore
from startup_theme import (
    ThemeFamilySelector,
    THEME_SELECTOR_POSITION_SETTINGS_KEY,
    ensure_startup_theme_preference,
    save_theme_family_preference,
    startup_theme_prompt_required,
)
from sound_tab import ArtworkButton as SoundArtworkButton
from ui_fonts import (
    COMMAND_FONT_FAMILY,
    REQUIRED_APPLICATION_FONT_FAMILIES,
    load_application_fonts,
    resolve_registered_family,
)
from ui_theme import (
    APPLICATION_FONT_ROLE,
    BASIC_DARK_THEME,
    BASIC_LIGHT_THEME,
    BASIC_BUTTON_TIER_STYLES,
    BUTTON_TIER_STYLES,
    CELESTIAL_ROSE_THEME,
    CYBER_FORGE_THEME,
    DEFAULT_THEME_ID,
    FUTURISTIC_THEME,
    HELP_THEME_ASSET_CANDIDATES,
    LETTER_CONTENT_FONT_ROLE,
    MAXIMIZE_THEME_ASSET,
    OBSIDIAN_FORGE_THEME,
    REQUIRED_CONTENT_THEME_ASSETS,
    REQUIRED_THEME_ASSETS,
    RESTORE_THEME_ASSET_CANDIDATES,
    SOFT_ELEGANT_THEME,
    TAB_HEADING_FONT_POINT_SIZE,
    THEMES,
    THEME_FONT_ROLE_PROPERTY,
    THEME_FAMILY_SETTINGS_KEY,
    THEME_SETTINGS_KEY,
    USER_ENTRY_FONT_ROLE,
    VELVET_ROSE_THEME,
    ButtonTier,
    ThemeDefinition,
    ThemeService,
    apply_button_tier,
    apply_tab_heading_style,
    install_button_text_guard,
    minimum_button_text_size,
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
            {
                "cyber_forge",
                "obsidian_forge",
                "velvet_rose",
                "celestial_rose",
                "dark",
                "light",
            },
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
            "secondary_surface",
            "control_border",
            "secondary_accent",
            "selected_background",
            "selected_text",
            "clear_action_background",
            "clear_action_hover",
            "clear_action_border",
            "heading_text",
            "disabled_text",
            "artwork_button_text",
            "artwork_button_disabled_text",
            "artwork_button_shadow",
            "preview_frame_border",
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
            CYBER_FORGE_THEME.tokens.accent = "#ffffff"  # type: ignore[misc]

    def test_primary_theme_palettes_have_distinct_light_dark_identities(self) -> None:
        self.assertEqual(
            (
                CYBER_FORGE_THEME.tokens.primary,
                CYBER_FORGE_THEME.tokens.secondary,
                CYBER_FORGE_THEME.tokens.background,
            ),
            ("#009eb8", "#7f9099", "#d4d4d4"),
        )
        self.assertEqual(
            (
                OBSIDIAN_FORGE_THEME.tokens.primary,
                OBSIDIAN_FORGE_THEME.tokens.secondary,
                OBSIDIAN_FORGE_THEME.tokens.background,
            ),
            ("#c99a3d", "#416dcc", "#0d0e10"),
        )
        self.assertEqual(
            (
                VELVET_ROSE_THEME.tokens.primary,
                VELVET_ROSE_THEME.tokens.secondary,
                VELVET_ROSE_THEME.tokens.background,
            ),
            ("#e05b91", "#c79a3b", "#d4bdc8"),
        )
        self.assertEqual(
            (
                CELESTIAL_ROSE_THEME.tokens.primary,
                CELESTIAL_ROSE_THEME.tokens.secondary,
                CELESTIAL_ROSE_THEME.tokens.background,
            ),
            ("#b89be8", "#e56fae", "#100b18"),
        )
        primary_light_themes = (
            CYBER_FORGE_THEME,
            VELVET_ROSE_THEME,
        )
        light_themes = (
            *primary_light_themes,
            BASIC_LIGHT_THEME,
        )
        brightness_ceiling = QtGui.QColor("#d4d4d4").lightness()
        self.assertEqual(
            len({theme.tokens.background for theme in primary_light_themes}),
            len(primary_light_themes),
        )
        for theme in light_themes:
            for token_field in fields(theme.tokens):
                color = QtGui.QColor(getattr(theme.tokens, token_field.name))
                self.assertLessEqual(color.lightness(), brightness_ceiling)
        self.assertLess(QtGui.QColor(OBSIDIAN_FORGE_THEME.tokens.background).lightness(), 40)
        self.assertLess(QtGui.QColor(CELESTIAL_ROSE_THEME.tokens.background).lightness(), 40)
        self.assertEqual(len({theme.tokens.clear_action_background for theme in (
            CYBER_FORGE_THEME,
            OBSIDIAN_FORGE_THEME,
            VELVET_ROSE_THEME,
            CELESTIAL_ROSE_THEME,
        )}), 4)
        self.assertEqual(len({theme.tokens.artwork_button_text for theme in (
            CYBER_FORGE_THEME,
            OBSIDIAN_FORGE_THEME,
            VELVET_ROSE_THEME,
            CELESTIAL_ROSE_THEME,
        )}), 4)
        self.assertEqual(
            {
                theme.theme_id: theme.tokens.preview_frame_border
                for theme in (
                    CYBER_FORGE_THEME,
                    OBSIDIAN_FORGE_THEME,
                    VELVET_ROSE_THEME,
                    CELESTIAL_ROSE_THEME,
                )
            },
            {
                "cyber_forge": "#009eb8",
                "obsidian_forge": "#c99a3d",
                "velvet_rose": "#c43865",
                "celestial_rose": "#725d89",
            },
        )

    def test_theme_definitions_own_exact_typography(self) -> None:
        expected = {
            "cyber_forge": ("IBM Plex Sans", "IBM Plex Sans"),
            "obsidian_forge": ("Rajdhani", "Cinzel"),
            "velvet_rose": ("Source Sans 3", "Source Sans 3"),
            "celestial_rose": ("Manrope", "Lora"),
            "dark": ("IBM Plex Sans", "Spectral"),
            "light": ("Source Sans 3", "Source Serif 4"),
        }

        self.assertEqual(
            {
                theme_id: (
                    definition.app_font_family,
                    definition.user_entry_font_family,
                )
                for theme_id, definition in THEMES.items()
            },
            expected,
        )
        self.assertEqual(COMMAND_FONT_FAMILY, "Press Start 2P")

    def test_repository_application_fonts_register_idempotently(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]

        first = load_application_fonts(repository_root)
        second = load_application_fonts(repository_root)

        self.assertIs(first, second)
        self.assertEqual(first.failed_files, ())
        self.assertEqual(first.missing_families, ())
        self.assertEqual(
            set(first.registered_families),
            set(REQUIRED_APPLICATION_FONT_FAMILIES),
        )
        for family in REQUIRED_APPLICATION_FONT_FAMILIES:
            with self.subTest(family=family):
                self.assertEqual(resolve_registered_family(family), family)

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

    def test_runtime_selection_rejects_unknown_theme_without_resetting_current(self) -> None:
        settings = SettingsStore(self.root)
        service = ThemeService(self.root, settings=settings)
        service.set_theme("velvet_rose")

        with self.assertRaisesRegex(ValueError, "Unknown theme identifier"):
            service.set_theme("velvet-rose")

        self.assertEqual(service.theme_id, "velvet_rose")
        self.assertEqual(settings.get(THEME_SETTINGS_KEY), "velvet_rose")

    def test_theme_registry_rejects_empty_and_duplicate_identifiers(self) -> None:
        with self.assertRaisesRegex(ValueError, DEFAULT_THEME_ID):
            ThemeService(self.root, themes={})
        with self.assertRaisesRegex(ValueError, "Duplicate theme identifier"):
            ThemeService(
                self.root,
                themes={
                    "CYBER_FORGE": CYBER_FORGE_THEME,
                    "cyber_forge": CYBER_FORGE_THEME,
                },
            )

    def test_theme_definition_rejects_non_finite_font_adjustment(self) -> None:
        for adjustment in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(adjustment=adjustment):
                with self.assertRaisesRegex(ValueError, "must be finite"):
                    ThemeDefinition(
                        theme_id="unsafe",
                        display_name="Unsafe",
                        tokens=FUTURISTIC_THEME.tokens,
                        font_size_adjustment=adjustment,
                    )

    def test_selection_persists_and_emits_once_per_actual_change(self) -> None:
        settings = SettingsStore(self.root)
        service = ThemeService(self.root, settings=settings)
        changes: list[tuple[str, ThemeDefinition]] = []
        service.theme_changed.connect(
            lambda theme_id, definition: changes.append((theme_id, definition))
        )

        selected = service.set_theme("VELVET_ROSE")
        service.set_theme("velvet_rose")
        snapshot = service.save()

        self.assertIs(selected, VELVET_ROSE_THEME)
        self.assertEqual(snapshot[THEME_SETTINGS_KEY], "velvet_rose")
        self.assertEqual(settings.get(THEME_SETTINGS_KEY), "velvet_rose")
        self.assertEqual(settings.get(THEME_FAMILY_SETTINGS_KEY), "rose")
        self.assertEqual(changes, [("velvet_rose", VELVET_ROSE_THEME)])

    def test_basic_themes_preserve_family_and_use_standard_buttons(self) -> None:
        settings = SettingsStore(self.root)
        settings.update_fields(
            {
                THEME_SETTINGS_KEY: "velvet_rose",
                THEME_FAMILY_SETTINGS_KEY: "rose",
            }
        )
        service = ThemeService(self.root, settings=settings)
        service.set_theme("dark")

        self.assertIs(service.current, BASIC_DARK_THEME)
        self.assertFalse(service.current.uses_image_buttons)
        self.assertEqual(settings.get(THEME_FAMILY_SETTINGS_KEY), "rose")

        button = ArtworkButton("Reset Images", self.root, "BButton.png")
        sound_button = SoundArtworkButton(
            "Stock",
            self.root,
            "AButton.png",
        )
        self.addCleanup(button.deleteLater)
        self.addCleanup(sound_button.deleteLater)
        button.apply_theme_assets(service)
        sound_button.apply_theme_assets(service)

        self.assertFalse(button.has_artwork)
        self.assertEqual(button.styleSheet(), "")
        self.assertTrue(sound_button._artwork.isNull())
        self.assertEqual(sound_button.styleSheet(), "")
        self.assertEqual(button.property("buttonTier"), "standard")
        self.assertEqual(sound_button.property("buttonTier"), "standard")
        tier_size = BUTTON_TIER_STYLES[ButtonTier.STANDARD].size
        for themed_button in (button, sound_button):
            required = minimum_button_text_size(themed_button)
            self.assertGreaterEqual(themed_button.width(), tier_size.width())
            self.assertGreaterEqual(themed_button.height(), tier_size.height())
            self.assertGreaterEqual(themed_button.width(), required.width())
            self.assertGreaterEqual(themed_button.height(), required.height())
        self.assertAlmostEqual(button.font().pointSizeF(), 14.3)
        self.assertAlmostEqual(sound_button.font().pointSizeF(), 14.3)
        self.assertTrue(button.font().bold())
        self.assertTrue(sound_button.font().bold())

        service.set_theme("light", persist=False)
        self.assertIs(service.current, BASIC_LIGHT_THEME)
        self.assertEqual(service.tokens.background, "#d4d4d4")
        self.assertEqual(service.tokens.primary, "#b8873e")
        self.assertEqual(service.tokens.secondary, "#c9a66b")
        self.assertEqual(service.tokens.accent, "#9a6b27")
        self.assertEqual(service.tokens.control_background, "#d0ccc4")
        self.assertEqual(service.tokens.active, "#d4bd87")
        self.assertEqual(service.tokens.text, "#241c13")
        self.assertEqual(service.tokens.hover, "#d4d4d4")
        for token_field in fields(service.tokens):
            color = QtGui.QColor(getattr(service.tokens, token_field.name))
            self.assertLessEqual(max(color.red(), color.green(), color.blue()), 0xD4)

    def test_button_tiers_own_geometry_and_typography(self) -> None:
        expected_tiers = {
            ButtonTier.LARGE: (308, 103, 13.7, 22, 12, 12),
            ButtonTier.STANDARD: (205, 68, 14.3, 21, 10, 10),
            ButtonTier.MEDIUM: (155, 51, 11.7, 16, 8, 9),
            ButtonTier.SMALL: (131, 44, 10.4, 12, 7, 8),
        }
        for tier, tier_style in BUTTON_TIER_STYLES.items():
            with self.subTest(tier=tier.value):
                self.assertEqual(
                    (
                        tier_style.width,
                        tier_style.height,
                        tier_style.font_point_size,
                        tier_style.horizontal_padding,
                        tier_style.vertical_padding,
                        tier_style.radius,
                    ),
                    expected_tiers[tier],
                )
                button = QtWidgets.QPushButton("Role")
                self.addCleanup(button.deleteLater)

                apply_button_tier(button, tier)

                self.assertEqual(button.size(), tier_style.size)
                self.assertEqual(button.minimumSize(), tier_style.size)
                self.assertEqual(button.maximumSize(), tier_style.size)
                self.assertEqual(button.property("buttonTier"), tier.value)
                self.assertEqual(
                    button.property("buttonWeight"),
                    "bold" if tier_style.bold else "regular",
                )
                self.assertAlmostEqual(
                    button.font().pointSizeF(),
                    tier_style.font_point_size,
                )
                self.assertTrue(tier_style.bold)
                self.assertTrue(button.font().bold())

        review = QtWidgets.QPushButton("Review")
        self.addCleanup(review.deleteLater)
        apply_button_tier(review, ButtonTier.SMALL, bold=True)
        self.assertEqual(review.property("buttonTier"), "small")
        self.assertEqual(review.property("buttonWeight"), "bold")
        self.assertTrue(review.font().bold())

    def test_velvet_rose_uses_readable_bold_small_buttons(self) -> None:
        service = ThemeService(self.root)
        root = QtWidgets.QWidget()
        small = QtWidgets.QPushButton("Review", root)
        apply_button_tier(small, ButtonTier.SMALL)
        self.addCleanup(root.deleteLater)

        service.set_theme("velvet_rose", persist=False)
        service.apply_semantic_styles(root)
        QtWidgets.QApplication.processEvents()

        self.assertAlmostEqual(small.font().pointSizeF(), 10.4)
        self.assertTrue(small.font().bold())
        self.assertIn('buttonTier="small"', root.styleSheet())
        self.assertIn("font-size:10.4pt", root.styleSheet())

        service.set_theme("celestial_rose", persist=False)
        service.apply_semantic_styles(root)
        QtWidgets.QApplication.processEvents()

        self.assertAlmostEqual(small.font().pointSizeF(), 10.4)
        self.assertTrue(small.font().bold())

    def test_tab_heading_style_uses_each_active_theme_font(self) -> None:
        root = QtWidgets.QWidget()
        root.setStyleSheet("QLabel{font:10pt 'Segoe UI';}")
        heading = QtWidgets.QLabel("Heading", root)
        self.addCleanup(root.deleteLater)
        apply_tab_heading_style(heading)

        self.assertEqual(
            heading.property(THEME_FONT_ROLE_PROPERTY),
            APPLICATION_FONT_ROLE,
        )
        self.assertEqual(heading.property("themeRole"), "headingText")
        self.assertAlmostEqual(
            heading.font().pointSizeF(),
            TAB_HEADING_FONT_POINT_SIZE,
        )
        self.assertTrue(heading.font().bold())

        service = ThemeService(self.root)
        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        ):
            service.set_theme(theme_id, persist=False)
            service.apply_semantic_styles(root)
            QtWidgets.QApplication.processEvents()
            with self.subTest(theme_id=theme_id):
                self.assertEqual(heading.font().family(), service.app_font_family)
                self.assertAlmostEqual(
                    heading.font().pointSizeF(),
                    TAB_HEADING_FONT_POINT_SIZE,
                )
                self.assertTrue(heading.font().bold())

    def test_long_button_labels_fit_every_primary_theme_font(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        load_application_fonts(repository)
        service = ThemeService(repository, settings=SettingsStore(self.root))
        labels = {
            ButtonTier.LARGE: ("Reset Images", "Gallery"),
            ButtonTier.STANDARD: ("Load Letters", "Revisions"),
            ButtonTier.MEDIUM: ("Replace Music", "Create Playlist"),
            ButtonTier.SMALL: ("GitHub Account", "Review"),
        }

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        ):
            service.set_theme(theme_id, persist=False)
            for tier, tier_labels in labels.items():
                style = BUTTON_TIER_STYLES[tier]
                font = QtGui.QFont(service.app_font_family)
                font.setPointSizeF(style.font_point_size)
                font.setBold(True)
                metrics = QtGui.QFontMetrics(font)
                horizontal_inset = 3 if tier is ButtonTier.SMALL else 8
                available_width = style.width - (horizontal_inset * 2)
                for label in tier_labels:
                    with self.subTest(
                        theme_id=theme_id,
                        tier=tier.value,
                        label=label,
                    ):
                        self.assertLessEqual(
                            metrics.horizontalAdvance(label),
                            available_width,
                        )

    def test_long_tier_label_expands_across_every_theme(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))
        button = QtWidgets.QPushButton("Copy Support Information")
        self.addCleanup(button.deleteLater)
        apply_button_tier(button, ButtonTier.SMALL)

        for theme_id in THEMES:
            service.set_theme(theme_id, persist=False)
            service.apply_semantic_styles(button)
            QtWidgets.QApplication.processEvents()
            required = minimum_button_text_size(button)
            with self.subTest(theme_id=theme_id):
                self.assertGreaterEqual(button.width(), required.width())
                self.assertGreaterEqual(button.height(), required.height())

    def test_runtime_guard_refits_a_dynamic_fixed_width_label(self) -> None:
        existing_guard = getattr(
            self.app,
            "_lettersmith_button_text_fit_guard",
            None,
        )
        guard = install_button_text_guard(self.app)
        if existing_guard is None and guard is not None:
            def remove_guard() -> None:
                self.app.removeEventFilter(guard)
                delattr(self.app, "_lettersmith_button_text_fit_guard")

            self.addCleanup(remove_guard)

        for button_type in (
            QtWidgets.QPushButton,
            QtWidgets.QToolButton,
            QtWidgets.QCheckBox,
            QtWidgets.QRadioButton,
        ):
            with self.subTest(button_type=button_type.__name__):
                button = button_type()
                button.setText("Ready")
                button.setFixedSize(80, 34)
                self.addCleanup(button.deleteLater)
                button.show()
                button.setText("Preparing Support Information...")
                QtWidgets.QApplication.processEvents()

                required = minimum_button_text_size(button)
                self.assertGreaterEqual(button.width(), required.width())
                self.assertGreaterEqual(button.height(), required.height())

    def test_basic_themes_use_compact_button_geometry(self) -> None:
        expected_basic_sizes = {
            ButtonTier.LARGE: QtCore.QSize(277, 82),
            ButtonTier.STANDARD: QtCore.QSize(184, 54),
            ButtonTier.MEDIUM: QtCore.QSize(140, 41),
            ButtonTier.SMALL: QtCore.QSize(118, 35),
        }
        self.assertEqual(
            {
                tier: style.size
                for tier, style in BASIC_BUTTON_TIER_STYLES.items()
            },
            expected_basic_sizes,
        )

        button = QtWidgets.QPushButton("Revisions")
        self.addCleanup(button.deleteLater)
        apply_button_tier(button, ButtonTier.STANDARD)
        repository = Path(__file__).resolve().parents[1]
        service = ThemeService(repository, settings=SettingsStore(self.root))

        for theme_id in ("dark", "light"):
            service.set_theme(theme_id, persist=False)
            service.apply_semantic_styles(button)
            self.assertEqual(
                button.size(),
                BASIC_BUTTON_TIER_STYLES[ButtonTier.STANDARD].size,
            )

        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        ):
            service.set_theme(theme_id, persist=False)
            service.apply_semantic_styles(button)
            with self.subTest(theme_id=theme_id):
                self.assertGreaterEqual(
                    button.width(),
                    BUTTON_TIER_STYLES[ButtonTier.STANDARD].width,
                )
                self.assertGreaterEqual(
                    button.height(),
                    BUTTON_TIER_STYLES[ButtonTier.STANDARD].height,
                )
                self.assertNotEqual(
                    button.size(),
                    BASIC_BUTTON_TIER_STYLES[ButtonTier.STANDARD].size,
                )

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

        service.set_theme("velvet_rose", persist=False)

        self.assertEqual(first.property("letterSmithTheme"), "velvet_rose")
        self.assertEqual(second.property("letterSmithTheme"), "velvet_rose")
        self.assertIn(VELVET_ROSE_THEME.tokens.accent, first.styleSheet())
        self.assertIn(VELVET_ROSE_THEME.tokens.accent, second.styleSheet())
        self.assertIn("QLabel#Extra", second.styleSheet())

    def test_themed_asset_override_wins_and_missing_asset_uses_cyber_baseline(self) -> None:
        themed = (
            self.root
            / "resources/app/themes/velvet_rose/prompt_writer/Pwrite.png"
        )
        themed.parent.mkdir(parents=True)
        themed.write_bytes(b"themed")
        baseline = (
            self.root
            / "resources/app/themes/cyber_forge/prompt_writer/Pwrite.png"
        )
        baseline.parent.mkdir(parents=True)
        baseline.write_bytes(b"baseline")

        service = ThemeService(self.root)
        service.set_theme("velvet_rose", persist=False)
        resolved = service.resolve_asset("prompt_writer/Pwrite.png")
        self.assertEqual(resolved, themed.resolve())

        themed.unlink()
        with self.assertLogs("ui_theme", level="INFO") as logged:
            self.assertEqual(
                service.resolve_asset("prompt_writer/Pwrite.png"),
                baseline.resolve(),
            )
        self.assertEqual(len(logged.output), 1)
        self.assertIn("fallback was found", logged.output[0])
        self.assertIn("Pwrite.png", logged.output[0])

    def test_missing_theme_asset_warns_only_when_all_fallbacks_are_missing(self) -> None:
        service = ThemeService(self.root)
        service.set_theme("velvet_rose", persist=False)

        with self.assertLogs("ui_theme", level="WARNING") as logged:
            missing = service.resolve_first_asset(
                ("help/hover.png",),
                fallback="icons/Help.png",
            )

        self.assertFalse(missing.is_file())
        self.assertEqual(len(logged.output), 1)
        self.assertIn("Theme asset and fallback are missing", logged.output[0])

    def test_help_assets_prefer_idle_gif_and_hover_png(self) -> None:
        themed_help = self.root / "resources/app/themes/velvet_rose/help"
        baseline_help = self.root / "resources/app/themes/cyber_forge/help"
        themed_help.mkdir(parents=True)
        baseline_help.mkdir(parents=True)
        themed_png = themed_help / "Help.png"
        themed_gif = themed_help / "Help.gif"
        themed_hover_png = themed_help / "HHelp.png"
        themed_hover_gif = themed_help / "HHelp.gif"
        baseline_gif = baseline_help / "Help.gif"
        themed_png.write_bytes(b"png")
        themed_hover_png.write_bytes(b"png")
        themed_hover_gif.write_bytes(b"gif")
        baseline_gif.write_bytes(b"gif")

        service = ThemeService(self.root)
        service.set_theme("velvet_rose", persist=False)
        candidates = HELP_THEME_ASSET_CANDIDATES["idle"]

        self.assertEqual(
            service.resolve_first_asset(candidates),
            themed_png.resolve(),
        )
        self.assertEqual(
            service.resolve_first_asset(HELP_THEME_ASSET_CANDIDATES["hover"]),
            themed_hover_png.resolve(),
        )
        themed_hover_png.unlink()
        themed_hover_gif.unlink()
        with self.assertLogs("ui_theme", level="INFO") as logged:
            self.assertEqual(
                service.resolve_first_asset(HELP_THEME_ASSET_CANDIDATES["hover"]),
                themed_png.resolve(),
            )
        self.assertEqual(len(logged.output), 1)
        self.assertIn("fallback was found", logged.output[0])
        self.assertIn("Help.png", logged.output[0])
        themed_gif.write_bytes(b"gif")
        self.assertEqual(
            service.resolve_first_asset(candidates),
            themed_gif.resolve(),
        )
        themed_gif.unlink()
        themed_png.unlink()
        self.assertEqual(
            service.resolve_first_asset(candidates),
            baseline_gif.resolve(),
        )

    def test_restore_asset_prefers_rest_then_falls_back_to_maxi(self) -> None:
        titlebar = self.root / "resources/app/themes/cyber_forge/titlebar"
        titlebar.mkdir(parents=True)
        maximize = titlebar / "maxi.png"
        restore = titlebar / "rest.png"
        maximize.write_bytes(b"maximize")
        service = ThemeService(self.root)

        self.assertEqual(
            service.resolve_first_asset(RESTORE_THEME_ASSET_CANDIDATES),
            maximize.resolve(),
        )
        restore.write_bytes(b"restore")
        self.assertEqual(
            service.resolve_first_asset(RESTORE_THEME_ASSET_CANDIDATES),
            restore.resolve(),
        )
        self.assertEqual(MAXIMIZE_THEME_ASSET, "titlebar/maximize.png")

    def test_each_active_theme_resolves_its_own_artwork_path(self) -> None:
        for theme_id in THEMES:
            path = (
                self.root
                / "resources/app/themes"
                / theme_id
                / "image_frame.png"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(theme_id.encode("ascii"))

        service = ThemeService(self.root)
        for theme_id in THEMES:
            with self.subTest(theme_id=theme_id):
                service.set_theme(theme_id, persist=False)
                expected = (
                    self.root
                    / "resources/app/themes"
                    / theme_id
                    / "image_frame.png"
                )
                self.assertEqual(
                    service.resolve_asset("image_frame.png"),
                    expected.resolve(),
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

    def test_button_asset_rejects_unsafe_identifiers_and_link_escape(self) -> None:
        service = ThemeService(self.root)
        for unsafe in (
            "..",
            "nested/AButton.png",
            "AButton.png:payload",
            "AButton\x00.png",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    service.resolve_button_asset(unsafe)

        outside = self.root / "outside-buttons"
        outside.mkdir()
        (outside / "AButton.png").write_bytes(b"outside")
        theme_root = self.root / "resources/app/themes/velvet_rose"
        theme_root.mkdir(parents=True)
        try:
            os.symlink(
                outside,
                theme_root / "buttons",
                target_is_directory=True,
            )
        except OSError as error:
            self.skipTest(f"Directory symlinks are unavailable: {error}")

        service.set_theme("velvet_rose", persist=False)
        with self.assertRaisesRegex(ValueError, "escapes its allowed directory"):
            service.resolve_button_asset("AButton.png", allow_baseline=False)

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
        self.assertIn('themeRole="clearAction"', qss)
        self.assertIn(service.tokens.clear_action_background, qss)
        self.assertIn(service.tokens.clear_action_border, qss)
        self.assertIn('themeFontRole="application"', qss)
        self.assertIn('themeFontRole="userEntry"', qss)
        self.assertIn("QPushButton, QToolButton { text-align: center; }", qss)

    def test_semantic_typography_roles_preserve_formatting_and_switch_live(self) -> None:
        load_application_fonts(Path(__file__).resolve().parents[1])
        service = ThemeService(self.root)
        root = QtWidgets.QWidget()
        app_label = QtWidgets.QLabel("Interface", root)
        app_label.setProperty(THEME_FONT_ROLE_PROPERTY, APPLICATION_FONT_ROLE)
        app_font = QtGui.QFont("Segoe UI", 13)
        app_font.setWeight(QtGui.QFont.Weight.Bold)
        app_label.setFont(app_font)

        user_entry = QtWidgets.QLineEdit(root)
        user_entry.setProperty(THEME_FONT_ROLE_PROPERTY, USER_ENTRY_FONT_ROLE)
        entry_font = QtGui.QFont("Segoe UI", 11)
        entry_font.setItalic(True)
        user_entry.setFont(entry_font)

        letter_content = QtWidgets.QWidget(root)
        letter_content.setProperty(
            THEME_FONT_ROLE_PROPERTY,
            LETTER_CONTENT_FONT_ROLE,
        )
        letter_child = QtWidgets.QLabel("Letter", letter_content)
        letter_font = QtGui.QFont("Papyrus", 17)
        letter_font.setUnderline(True)
        letter_child.setFont(letter_font)

        independent = QtWidgets.QWidget(root)
        independent.setProperty("themeIndependent", True)
        independent_child = QtWidgets.QLabel("Independent", independent)
        independent_font = QtGui.QFont("Consolas", 15)
        independent_child.setFont(independent_font)
        self.addCleanup(root.deleteLater)

        expected = {
            "cyber_forge": ("IBM Plex Sans", "IBM Plex Sans"),
            "obsidian_forge": ("Rajdhani", "Cinzel"),
            "velvet_rose": ("Source Sans 3", "Source Sans 3"),
            "celestial_rose": ("Manrope", "Lora"),
            "light": ("Source Sans 3", "Source Serif 4"),
            "dark": ("IBM Plex Sans", "Spectral"),
        }
        for theme_id, (app_family, entry_family) in expected.items():
            service.set_theme(theme_id, persist=False)
            service.apply_semantic_styles(root)

            with self.subTest(theme_id=theme_id):
                self.assertEqual(app_label.font().family(), app_family)
                self.assertEqual(app_label.font().pointSize(), 13)
                self.assertEqual(
                    app_label.font().weight(),
                    QtGui.QFont.Weight.Bold,
                )
                self.assertEqual(user_entry.font().family(), entry_family)
                self.assertEqual(user_entry.font().pointSize(), 11)
                self.assertTrue(user_entry.font().italic())
                self.assertEqual(letter_child.font().family(), "Papyrus")
                self.assertEqual(letter_child.font().pointSize(), 17)
                self.assertTrue(letter_child.font().underline())
                self.assertEqual(independent_child.font().family(), "Consolas")

    def test_qss_font_translation_is_narrow_and_role_aware(self) -> None:
        load_application_fonts(Path(__file__).resolve().parents[1])
        service = ThemeService(self.root)
        root = QtWidgets.QWidget()
        interface = QtWidgets.QLabel("Interface", root)
        interface.setStyleSheet(
            "QLabel { font-family: 'Segoe UI'; font-size: 19px; "
            "font-weight: 600; }"
        )
        semibold = QtWidgets.QLabel("Semibold", root)
        semibold.setStyleSheet(
            "QLabel { font-family: Segoe UI Semibold; }"
        )
        special = QtWidgets.QLabel("Special", root)
        special.setStyleSheet(
            "QLabel { font-family: 'Segoe UI Symbol'; }"
            "QLabel#Emoji { font-family: Segoe UI Emoji; }"
            "QLabel#Other { font-family: Consolas; }"
        )
        content = QtWidgets.QLabel("Content", root)
        content.setProperty(THEME_FONT_ROLE_PROPERTY, LETTER_CONTENT_FONT_ROLE)
        content.setStyleSheet("QLabel { font-family: 'Segoe UI'; }")
        inline = QtWidgets.QLabel("Inline", root)
        inline.setStyleSheet("font:700 12pt 'Segoe UI';")
        self.addCleanup(root.deleteLater)

        service.apply_semantic_styles(root)

        self.assertIn("'IBM Plex Sans'", interface.styleSheet())
        self.assertIn("font-size: 19px", interface.styleSheet())
        self.assertIn("font-weight: 600", interface.styleSheet())
        self.assertIn("'IBM Plex Sans'", semibold.styleSheet())
        self.assertIn("'Segoe UI Symbol'", special.styleSheet())
        self.assertIn("Segoe UI Emoji", special.styleSheet())
        self.assertIn("Consolas", special.styleSheet())
        self.assertIn("'Segoe UI'", content.styleSheet())
        self.assertIn("'IBM Plex Sans'", inline.styleSheet())
        self.assertNotIn("*[themeFontRole", inline.styleSheet())

    def test_semantic_style_translation_recolors_legacy_blue_for_every_theme(self) -> None:
        service = ThemeService(self.root)
        root = QtWidgets.QWidget()
        root.setObjectName("OrdinaryTestRoot")
        button = QtWidgets.QPushButton("Test", root)
        button.setStyleSheet(
            "QPushButton{color:#edf7fb;border:1px solid #00d0ff;"
            "background:#101317;}"
        )
        self.addCleanup(root.deleteLater)

        translated_styles = set()
        for theme_id in (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        ):
            service.set_theme(theme_id, persist=False)
            service.apply_semantic_styles(root)
            translated = button.styleSheet().casefold()
            translated_styles.add(translated)
            self.assertNotIn("#00d0ff", translated)
            self.assertIn(service.tokens.accent_secondary, translated)
            self.assertIn(service.tokens.panel_background, translated)
        self.assertEqual(len(translated_styles), 4)

    def test_semantic_text_colors_follow_light_and_dark_after_runtime_updates(self) -> None:
        service = ThemeService(self.root)
        root = QtWidgets.QWidget()
        label = QtWidgets.QLabel("Existing", root)
        label.setStyleSheet("QLabel{color:#fff;}")
        self.addCleanup(root.deleteLater)

        service.set_theme("light", persist=False)
        service.apply_semantic_styles(root)
        self.assertIn(BASIC_LIGHT_THEME.tokens.text, label.styleSheet())

        label.setStyleSheet("QLabel{color:#eee;}")
        late_label = QtWidgets.QLabel("Late", root)
        late_label.setStyleSheet("QLabel{color:#ddd;}")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.processEvents()
        self.assertIn(BASIC_LIGHT_THEME.tokens.text, label.styleSheet())
        self.assertIn(BASIC_LIGHT_THEME.tokens.text, late_label.styleSheet())

        service.set_theme("dark", persist=False)
        label.setStyleSheet("QLabel{color:#111;}")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.processEvents()
        self.assertIn(BASIC_DARK_THEME.tokens.text, label.styleSheet())

    def test_artwork_button_reloads_theme_specific_cloud_asset(self) -> None:
        fallback = (
            self.root
            / "resources/app/themes/cyber_forge/buttons/BButton.png"
        )
        themed = (
            self.root
            / "resources/app/themes/velvet_rose/buttons/BButton.png"
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

        service.set_theme("velvet_rose", persist=False)
        button.apply_theme_assets(service)
        self.assertEqual(button.artwork_path, themed.resolve())

    def test_artwork_button_uses_each_theme_text_roles(self) -> None:
        button = ArtworkButton("Preview Letter", self.root, "ALbutton.png")
        self.addCleanup(button.deleteLater)
        primary_themes = (
            CYBER_FORGE_THEME,
            OBSIDIAN_FORGE_THEME,
            VELVET_ROSE_THEME,
            CELESTIAL_ROSE_THEME,
        )

        for theme in primary_themes:
            button.setEnabled(True)
            button._commit_requested_action_state()
            button._theme_tokens = theme.tokens
            self.assertEqual(
                button._artwork_label_color(),
                QtGui.QColor(theme.tokens.artwork_button_text),
            )
            self.assertEqual(
                button._artwork_shadow_color().name(),
                QtGui.QColor(theme.tokens.artwork_button_shadow).name(),
            )

            button.setEnabled(False)
            self.assertEqual(
                button.visual_semantic_state,
                ButtonSemanticState.NORMAL,
            )
            self.assertTrue(button.is_invisible)
            button._commit_requested_action_state()
            button._theme_tokens = theme.tokens
            self.assertEqual(
                button._artwork_label_color(),
                QtGui.QColor(theme.tokens.artwork_button_disabled_text),
            )

    def test_all_custom_themes_switch_normal_broken_connected_and_long_art(self) -> None:
        custom_themes = (
            "cyber_forge",
            "obsidian_forge",
            "velvet_rose",
            "celestial_rose",
        )
        for theme_id in custom_themes:
            root = self.root / "resources/app/themes" / theme_id / "buttons"
            for relative in (
                "AButton.png",
                "broken/AButton.png",
                "broken/ConnectButton.png",
                "long/ALbutton.png",
                "long/broken/ALbutton.png",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                self.assertTrue(
                    QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32).save(str(path))
                )

        service = ThemeService(self.root)
        standard = ArtworkButton(
            "Clear",
            self.root,
            "AButton.png",
            broken_artwork_filename="AButton.png",
        )
        connected = ArtworkButton(
            "GitHub Account",
            self.root,
            "BButton.png",
            broken_artwork_filename="ConnectButton.png",
        )
        long_button = ArtworkButton(
            "Preview Letter",
            self.root,
            "ALbutton.png",
            broken_artwork_filename="ALbutton.png",
            long_form=True,
        )
        for button in (standard, connected, long_button):
            self.addCleanup(button.deleteLater)

        connected.set_presentation_artwork("ConnectButton.png", broken=True)
        long_button.set_action_state(
            broken=True,
            invisible=False,
        )
        long_button._commit_requested_action_state()
        for theme_id in custom_themes:
            service.set_theme(theme_id, persist=False)
            for button in (standard, connected, long_button):
                button.apply_theme_assets(service)
            self.assertEqual(standard.artwork_path.name.casefold(), "abutton.png")
            self.assertNotIn("broken", standard.artwork_path.parts)
            standard.setEnabled(False)
            standard._commit_requested_action_state()
            self.assertEqual(
                standard.visual_semantic_state,
                ButtonSemanticState.NORMAL,
            )
            self.assertTrue(standard.is_invisible)
            self.assertNotIn(
                "broken",
                tuple(part.casefold() for part in standard.artwork_path.parts),
            )
            standard.set_action_state(
                broken=True,
                invisible=True,
            )
            standard._commit_requested_action_state()
            self.assertEqual(
                standard.visual_semantic_state,
                ButtonSemanticState.BROKEN,
            )
            self.assertIn(
                "broken",
                tuple(part.casefold() for part in standard.artwork_path.parts),
            )
            standard.set_action_state(
                broken=False,
                invisible=False,
            )
            standard._commit_requested_action_state()
            self.assertIn(theme_id, standard.artwork_path.parts)
            self.assertEqual(connected.artwork_path.name.casefold(), "connectbutton.png")
            self.assertIn(theme_id, connected.artwork_path.parts)
            self.assertEqual(long_button.artwork_path.name.casefold(), "albutton.png")
            lowered = tuple(part.casefold() for part in long_button.artwork_path.parts)
            self.assertIn("long", lowered)
            self.assertIn("broken", lowered)

    def test_artwork_semantic_state_debounces_and_crossfades(self) -> None:
        button_root = self.root / "resources/app/themes/cyber_forge/buttons"
        normal = button_root / "AButton.png"
        broken = button_root / "broken/AButton.png"
        broken.parent.mkdir(parents=True, exist_ok=True)
        normal_image = QtGui.QImage(12, 8, QtGui.QImage.Format_ARGB32)
        normal_image.fill(QtGui.QColor("#ff0000"))
        broken_image = QtGui.QImage(12, 8, QtGui.QImage.Format_ARGB32)
        broken_image.fill(QtGui.QColor("#0000ff"))
        self.assertTrue(normal_image.save(str(normal)))
        self.assertTrue(broken_image.save(str(broken)))
        service = ThemeService(self.root)
        button = ArtworkButton(
            "Clear",
            self.root,
            "AButton.png",
            broken_artwork_filename="AButton.png",
        )
        self.addCleanup(button.deleteLater)
        button.apply_theme_assets(service)
        button.setText("")
        button.set_artwork_stretch(True)
        button.resize(120, 60)
        button.show()
        self.app.processEvents()

        button.set_action_state(
            broken=True,
            invisible=False,
        )
        self.assertFalse(button.isEnabled())
        self.assertTrue(button._state_delay_timer.isActive())
        self.assertEqual(button._state_delay_timer.interval(), 1500)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertNotIn("broken", button.artwork_path.as_posix().casefold())

        button.set_action_state(
            broken=False,
            invisible=False,
        )
        self.assertFalse(button._state_delay_timer.isActive())
        self.assertTrue(button.isEnabled())
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )

        button.set_action_state(
            broken=True,
            invisible=False,
        )
        button._commit_requested_action_state()
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertIn("broken", button.artwork_path.as_posix().casefold())
        self.assertEqual(button._artwork_transition.duration(), 400)
        self.assertFalse(button._artwork_transition_from.isNull())
        self.assertFalse(button._artwork_transition_to.isNull())

        button._artwork_transition.setCurrentTime(200)
        self.app.processEvents()
        midpoint = button.grab().toImage().pixelColor(button.rect().center())
        self.assertGreater(midpoint.red(), 40)
        self.assertGreater(midpoint.blue(), 40)

        button._artwork_transition.setCurrentTime(
            button._artwork_transition.duration()
        )
        broken_path = button.artwork_path
        button.set_action_state(broken=True, invisible=True)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertEqual(button.artwork_path, broken_path)
        self.assertTrue(button.is_invisible)
        self.assertFalse(button.isEnabled())
        self.assertEqual(
            button._opacity_transition.endValue(),
            0.52,
        )

        button.set_action_state(broken=False, invisible=True)
        self.assertEqual(button.semantic_state, ButtonSemanticState.NORMAL)
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.BROKEN,
        )
        self.assertFalse(button._state_delay_timer.isActive())

        button.set_action_state(broken=False, invisible=False)
        self.assertFalse(button._state_delay_timer.isActive())
        self.assertEqual(
            button.visual_semantic_state,
            ButtonSemanticState.NORMAL,
        )
        self.assertNotIn("broken", button.artwork_path.as_posix().casefold())

    def test_missing_themed_button_uses_semantic_control_not_cyber_artwork(self) -> None:
        baseline = (
            self.root
            / "resources/app/themes/cyber_forge/buttons/BButton.png"
        )
        baseline.parent.mkdir(parents=True)
        self.assertTrue(
            QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32).save(str(baseline))
        )
        service = ThemeService(self.root)
        button = ArtworkButton("Clear", self.root, "BButton.png")
        self.addCleanup(button.deleteLater)

        service.set_theme("velvet_rose", persist=False)
        button.apply_theme_assets(service)

        self.assertFalse(button.has_artwork)
        self.assertEqual(button.styleSheet(), "")
        self.assertIn("velvet_rose", button.artwork_path.as_posix())

    def test_legacy_theme_ids_migrate_to_named_theme_defaults(self) -> None:
        settings = SettingsStore(self.root)
        settings.update_fields({THEME_SETTINGS_KEY: "soft_elegant"})

        service = ThemeService(self.root, settings=settings)

        self.assertEqual(service.theme_id, "velvet_rose")
        self.assertEqual(settings.get(THEME_SETTINGS_KEY), "velvet_rose")
        self.assertEqual(settings.get(THEME_FAMILY_SETTINGS_KEY), "rose")

    def test_startup_prompt_development_and_production_behavior(self) -> None:
        settings = SettingsStore(self.root)
        self.assertTrue(
            startup_theme_prompt_required(settings, force_prompt=False)
        )

        class FemaleDialog:
            selected_family = "rose"

            def __init__(self, _root) -> None:
                pass

            def exec(self) -> int:
                return QtWidgets.QDialog.Accepted

        selected = ensure_startup_theme_preference(
            self.root,
            force_prompt=False,
            dialog_factory=FemaleDialog,
        )
        self.assertEqual(selected, "velvet_rose")
        self.assertFalse(
            startup_theme_prompt_required(settings, force_prompt=False)
        )

        calls = 0

        class MaleDialog:
            selected_family = "forge"

            def __init__(self, _root) -> None:
                nonlocal calls
                calls += 1

            def exec(self) -> int:
                return QtWidgets.QDialog.Accepted

        selected = ensure_startup_theme_preference(
            self.root,
            force_prompt=True,
            dialog_factory=MaleDialog,
        )
        self.assertEqual(selected, "cyber_forge")
        self.assertEqual(calls, 1)

        selected = ensure_startup_theme_preference(
            self.root,
            force_prompt=True,
            dialog_factory=FemaleDialog,
        )
        self.assertEqual(selected, "velvet_rose")

        selected = ensure_startup_theme_preference(
            self.root,
            force_prompt=False,
            dialog_factory=FemaleDialog,
        )
        self.assertEqual(selected, "velvet_rose")
        self.assertEqual(calls, 1)

        settings.update_fields(
            {
                THEME_FAMILY_SETTINGS_KEY: "rose",
                THEME_SETTINGS_KEY: "celestial_rose",
            }
        )
        selected = ensure_startup_theme_preference(
            self.root,
            force_prompt=False,
            dialog_factory=FemaleDialog,
        )
        self.assertEqual(selected, "celestial_rose")

    def test_startup_family_selection_uses_system_appearance(self) -> None:
        settings = SettingsStore(self.root)
        expected_themes = {
            ("forge", False): "cyber_forge",
            ("forge", True): "obsidian_forge",
            ("rose", False): "velvet_rose",
            ("rose", True): "celestial_rose",
        }

        for (family, dark_mode), expected_theme in expected_themes.items():
            with self.subTest(family=family, dark_mode=dark_mode):
                with mock.patch(
                    "startup_theme._system_uses_dark_mode",
                    return_value=dark_mode,
                ):
                    selected = save_theme_family_preference(settings, family)
                self.assertEqual(selected, expected_theme)
                self.assertEqual(
                    settings.get(THEME_FAMILY_SETTINGS_KEY),
                    family,
                )
                self.assertEqual(
                    settings.get(THEME_SETTINGS_KEY),
                    expected_theme,
                )

    def test_startup_choice_buttons_are_identically_sized(self) -> None:
        icons = self.root / "gallery/app/icons"
        icons.mkdir(parents=True)
        for filename in ("mal.png", "fem.png"):
            self.assertTrue(
                QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32).save(
                    str(icons / filename)
                )
            )
        selector = ThemeFamilySelector(self.root)
        self.addCleanup(selector.deleteLater)
        self.assertEqual(selector.male_button.size(), selector.female_button.size())
        self.assertEqual(selector.question_label.text(), "Are you male or female?")
        self.assertEqual(selector.windowTitle(), "")
        self.assertTrue(selector.windowFlags() & QtCore.Qt.FramelessWindowHint)
        self.assertTrue(selector.windowFlags() & QtCore.Qt.Popup)
        self.assertEqual(selector.male_button.text(), "")
        self.assertEqual(selector.female_button.text(), "")
        self.assertEqual(selector.male_button.iconSize(), QtCore.QSize(500, 500))
        self.assertEqual(selector.choice_divider.width(), 1)
        self.assertTrue(
            selector.question_label.testAttribute(
                QtCore.Qt.WA_TransparentForMouseEvents
            )
        )
        self.assertTrue(
            selector.choice_divider.testAttribute(
                QtCore.Qt.WA_TransparentForMouseEvents
            )
        )
        male_glow = selector.male_button.graphicsEffect()
        female_glow = selector.female_button.graphicsEffect()
        self.assertIsInstance(male_glow, QtWidgets.QGraphicsDropShadowEffect)
        self.assertIsInstance(female_glow, QtWidgets.QGraphicsDropShadowEffect)
        self.assertEqual(male_glow.color(), QtGui.QColor("#2b8cff"))
        self.assertEqual(female_glow.color(), QtGui.QColor("#ff4fa3"))
        self.assertFalse(male_glow.isEnabled())
        self.assertFalse(female_glow.isEnabled())

        outside_selector = ThemeFamilySelector(self.root)
        self.addCleanup(outside_selector.deleteLater)
        outside_selector.show()
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.sendEvent(
            outside_selector,
            QtCore.QEvent(QtCore.QEvent.WindowDeactivate),
        )
        self.assertFalse(outside_selector.isVisible())
        self.assertEqual(outside_selector.result(), QtWidgets.QDialog.Rejected)

        QtTest.QTest.mousePress(
            selector.male_button,
            QtCore.Qt.LeftButton,
            pos=selector.male_button.rect().center(),
        )
        selector.male_button._press_animation.setCurrentTime(
            selector.male_button._press_animation.duration()
        )
        self.app.processEvents()
        self.assertEqual(selector.selected_family, "")
        self.assertEqual(selector.male_button.iconSize(), QtCore.QSize(450, 450))
        QtTest.QTest.mouseRelease(
            selector.male_button,
            QtCore.Qt.LeftButton,
            pos=QtCore.QPoint(-10, -10),
        )
        selector.male_button._restore_animation.setCurrentTime(
            selector.male_button._restore_animation.duration()
        )
        self.app.processEvents()
        self.assertEqual(selector.selected_family, "")
        self.assertEqual(selector.male_button.iconSize(), QtCore.QSize(500, 500))

        QtTest.QTest.mouseClick(
            selector.female_button,
            QtCore.Qt.LeftButton,
            pos=selector.female_button.rect().center(),
        )
        self.assertEqual(selector.selected_family, "rose")
        self.assertEqual(selector.result(), QtWidgets.QDialog.Accepted)

        QtWidgets.QApplication.sendEvent(
            selector.male_button,
            QtCore.QEvent(QtCore.QEvent.Enter),
        )
        QtWidgets.QApplication.sendEvent(
            selector.female_button,
            QtCore.QEvent(QtCore.QEvent.Enter),
        )
        self.assertTrue(male_glow.isEnabled())
        self.assertTrue(female_glow.isEnabled())

        QtWidgets.QApplication.sendEvent(
            selector.male_button,
            QtCore.QEvent(QtCore.QEvent.Leave),
        )
        QtWidgets.QApplication.sendEvent(
            selector.female_button,
            QtCore.QEvent(QtCore.QEvent.Leave),
        )
        self.assertFalse(male_glow.isEnabled())
        self.assertFalse(female_glow.isEnabled())

    def test_startup_choices_fit_a_short_screen(self) -> None:
        selector = ThemeFamilySelector(self.root)
        self.addCleanup(selector.deleteLater)
        selector.resize(1100, 720)
        screen = mock.Mock()
        screen.availableGeometry.return_value = QtCore.QRect(0, 0, 854, 492)
        with mock.patch("window_chrome.screen_for_launcher", return_value=screen):
            selector.show()
            self.app.processEvents()
            for width, height in ((854, 492), (680, 400)):
                selector.resize(width, height)
                self.app.processEvents()
                with self.subTest(size=(width, height)):
                    for widget in (
                        selector.male_button,
                        selector.female_button,
                        selector.choice_divider,
                    ):
                        bounds = QtCore.QRect(
                            widget.mapTo(selector, QtCore.QPoint()), widget.size()
                        )
                        self.assertTrue(selector.rect().contains(bounds), bounds)
                    self.assertEqual(
                        selector.male_button.iconSize().height(),
                        selector.male_button.height() - 12,
                    )
                    self.assertEqual(
                        selector.male_button.size(), selector.female_button.size()
                    )
        selector.reject()

    def test_startup_theme_selector_can_be_dragged(self) -> None:
        selector = ThemeFamilySelector(self.root)
        self.addCleanup(selector.deleteLater)
        selector.move(100, 120)
        local_position = QtCore.QPointF(40, 30)
        press_global = QtCore.QPointF(140, 150)
        move_global = QtCore.QPointF(205, 195)

        selector.mousePressEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonPress,
                local_position,
                press_global,
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
            )
        )
        selector.mouseMoveEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseMove,
                local_position,
                move_global,
                QtCore.Qt.NoButton,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
            )
        )

        self.assertEqual(selector.pos(), QtCore.QPoint(165, 165))

        selector.mouseReleaseEvent(
            QtGui.QMouseEvent(
                QtCore.QEvent.MouseButtonRelease,
                local_position,
                move_global,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
                QtCore.Qt.NoModifier,
            )
        )
        self.assertIsNone(selector._drag_offset)
        self.assertEqual(
            SettingsStore(self.root).get(THEME_SELECTOR_POSITION_SETTINGS_KEY),
            {"x": 165, "y": 165},
        )

        restored = ThemeFamilySelector(self.root)
        self.addCleanup(restored.deleteLater)
        available = QtCore.QRect(0, 0, 3840, 2160)
        screen = mock.Mock()
        screen.availableGeometry.return_value = available
        with mock.patch(
            "window_chrome.screen_for_launcher",
            return_value=screen,
        ):
            restored._restore_saved_position()
        self.assertEqual(restored.pos(), QtCore.QPoint(165, 165))

    def test_startup_theme_selector_rejects_click_outside(self) -> None:
        selector = ThemeFamilySelector(self.root)
        outside = QtWidgets.QWidget()
        self.addCleanup(selector.deleteLater)
        self.addCleanup(outside.deleteLater)
        selector.show()
        self.app.processEvents()
        outside_position = selector.frameGeometry().bottomRight() + QtCore.QPoint(
            20,
            20,
        )
        outside_press = QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress,
            QtCore.QPointF(5, 5),
            QtCore.QPointF(outside_position),
            QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        )

        QtWidgets.QApplication.sendEvent(outside, outside_press)

        self.assertFalse(selector.isVisible())
        self.assertEqual(selector.result(), QtWidgets.QDialog.Rejected)

    def test_startup_theme_selector_saves_position_when_closed(self) -> None:
        selector = ThemeFamilySelector(self.root)
        self.addCleanup(selector.deleteLater)
        selector.show()
        self.app.processEvents()
        selector.move(240, 180)

        selector.reject()
        self.app.processEvents()

        self.assertEqual(
            SettingsStore(self.root).get(THEME_SELECTOR_POSITION_SETTINGS_KEY),
            {"x": 240, "y": 180},
        )

    def test_startup_choice_accepts_dialog_instead_of_canceling_startup(self) -> None:
        self.assertFalse(FORCE_THEME_FAMILY_PROMPT)
        icons = self.root / "gallery/app/icons"
        icons.mkdir(parents=True)
        for filename in ("mal.png", "fem.png"):
            self.assertTrue(
                QtGui.QImage(24, 24, QtGui.QImage.Format_ARGB32).save(
                    str(icons / filename)
                )
            )
        selector = ThemeFamilySelector(self.root)
        self.addCleanup(selector.deleteLater)
        safety_timer = QtCore.QTimer(selector)
        safety_timer.setSingleShot(True)
        safety_timer.timeout.connect(selector.reject)
        safety_timer.start(1500)
        QtCore.QTimer.singleShot(
            0,
            lambda: QtTest.QTest.mouseClick(
                selector.male_button,
                QtCore.Qt.LeftButton,
                pos=selector.male_button.rect().center(),
            ),
        )

        result = selector.exec()

        self.assertEqual(result, QtWidgets.QDialog.Accepted)
        self.assertEqual(selector.selected_family, "forge")

    def test_repository_theme_button_resolution_never_uses_unrelated_art(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        for theme_id, definition in THEMES.items():
            if not definition.uses_image_buttons:
                continue
            settings = SettingsStore(self.root)
            service = ThemeService(repository, settings=settings)
            service.set_theme(theme_id, persist=False)
            resolved = service.resolve_button_asset(
                "AButton.png",
                allow_baseline=False,
            )
            self.assertEqual(resolved.name.casefold(), "abutton.png")
            self.assertIn(theme_id, resolved.parts)

    def test_repository_custom_themes_have_button_directories(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        for theme_id, definition in THEMES.items():
            if not definition.uses_image_buttons:
                continue
            root = repository / "resources/app/themes" / theme_id
            self.assertTrue((root / "buttons").is_dir(), theme_id)
            self.assertTrue((root / "buttons/complete.png").is_file(), theme_id)

    def test_dark_and_light_own_non_button_theme_assets(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        for theme_id in ("dark", "light"):
            root = repository / "resources/app/themes" / theme_id
            missing = tuple(
                relative
                for relative in REQUIRED_CONTENT_THEME_ASSETS
                if not (root / relative).is_file()
            )
            self.assertEqual(missing, (), theme_id)
            self.assertFalse((root / "buttons").exists())
            definition = THEMES[theme_id]
            for candidates in HELP_THEME_ASSET_CANDIDATES.values():
                self.assertTrue(
                    any(
                        (
                            root
                            / definition.asset_overrides.get(candidate, candidate)
                        ).is_file()
                        for candidate in candidates
                    ),
                    (theme_id, candidates),
                )

    def test_new_project_artwork_follows_all_six_themes(self) -> None:
        from Nexus import _ThemedNewProjectButton

        repository = Path(__file__).resolve().parents[1]
        parent = QtWidgets.QWidget()
        self.addCleanup(parent.deleteLater)
        button = _ThemedNewProjectButton(repository, parent)
        service = ThemeService(repository, settings=SettingsStore(self.root))

        for theme_id in THEMES:
            with self.subTest(theme=theme_id):
                service.set_theme(theme_id, persist=False)
                button.apply_theme_assets(service)
                self.assertEqual(
                    button.artwork_path,
                    (
                        repository
                        / "resources/app/themes"
                        / theme_id
                        / "New/New.png"
                    ).resolve(),
                )

    def test_legacy_button_helper_preserves_explicit_assignment(self) -> None:
        artwork_root = (
            self.root / "resources/app/themes/cyber_forge/buttons"
        )
        artwork_root.mkdir(parents=True)
        valid_names = {"AButton.png", "ROButton.png", "ABCButton.png"}
        invalid_names = {"Button.png", "ABCDButton.png", "A Button.png"}
        for name in valid_names | invalid_names:
            self.assertTrue(
                QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32).save(
                    str(artwork_root / name)
                )
            )

        discovered = discover_button_artwork_files(artwork_root)
        self.assertEqual({path.name for path in discovered}, valid_names)
        self.assertEqual(
            set(
                application_resource_names(
                    self.root / "resources/app",
                    "themes/cyber_forge/buttons",
                    ("AButton.png",),
                )
            ),
            valid_names,
        )

        assigned = tuple(
            session_button_artwork_path(artwork_root, key, "AButton.png")
            for key in ("images.reset", "message.import", "sound.archive")
        )
        self.assertEqual({path.name for path in assigned}, {"AButton.png"})
        self.assertEqual(
            session_button_artwork_path(
                artwork_root,
                "images.reset",
                "AButton.png",
            ),
            assigned[0],
        )

    def test_both_button_imports_preserve_fixed_assignments(self) -> None:
        artwork_root = (
            self.root / "resources/app/themes/cyber_forge/buttons"
        )
        artwork_root.mkdir(parents=True)
        names = tuple(f"{letter}Button.png" for letter in "ABCDEFGH")
        for name in names:
            self.assertTrue(
                QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32).save(
                    str(artwork_root / name)
                )
            )

        buttons = [
            ArtworkButton(
                f"Image or message {index}",
                self.root,
                name,
            )
            for index, name in enumerate(names[:5])
        ]
        buttons.extend(
            SoundArtworkButton(
                f"Sound {index}",
                self.root,
                name,
            )
            for index, name in enumerate(names[5:])
        )
        for button in buttons:
            self.addCleanup(button.deleteLater)

        self.assertEqual(
            [button.artwork_path.name for button in buttons],
            list(names),
        )


if __name__ == "__main__":
    unittest.main()
