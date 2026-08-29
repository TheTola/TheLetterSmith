from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from config import (
    APP_BANNER_PATH,
    CONTROL_FILES,
    PLAY_METADATA_FILE,
    REQUIRED_SLIDES,
)
import curtain_cache
import curtain_color
import generate
from readiness import ReadinessResult
from saved_letters import (
    SavedLetterCatalog,
    SavedLetterRestorer,
    update_saved_metadata,
)
from settings_store import (
    CURTAIN_STYLE_COMPLEMENTARY,
    CURTAIN_STYLE_COMPLEMENTARY_DARK,
    CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
    CURTAIN_STYLE_NORMAL,
    CURTAIN_STYLE_NORMAL_DARK,
    CURTAIN_STYLE_NORMAL_LIGHT,
    CURTAIN_STYLE_OPTIONS,
    CURTAIN_STYLE_WHITE,
    DEFAULT_CURTAIN_STYLE,
    SettingsStore,
)
from transactional_io import set_path_hidden


class CurtainCacheTests(unittest.TestCase):
    @staticmethod
    def _contrast_ratio(
        first: tuple[int, int, int],
        second: tuple[int, int, int],
    ) -> float:
        def luminance(rgb: tuple[int, int, int]) -> float:
            channels = []
            for channel in rgb:
                value = channel / 255.0
                channels.append(
                    value / 12.92
                    if value <= 0.04045
                    else ((value + 0.055) / 1.055) ** 2.4
                )
            return (
                0.2126 * channels[0]
                + 0.7152 * channels[1]
                + 0.0722 * channels[2]
            )

        first_luminance = luminance(first)
        second_luminance = luminance(second)
        lighter = max(first_luminance, second_luminance)
        darker = min(first_luminance, second_luminance)
        return (lighter + 0.05) / (darker + 0.05)

    def test_canonical_banner_source_exists(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.assertTrue((project_root / APP_BANNER_PATH).is_file())

    @staticmethod
    def _write_sources(root: Path) -> Path:
        pages = root / "gallery" / "user" / "pages"
        controls = root / "gallery" / "user" / "card" / "controls"
        pages.mkdir(parents=True)
        controls.mkdir(parents=True)
        (root / APP_BANNER_PATH.parent).mkdir(parents=True)

        cover = Image.new("RGBA", (18, 18), (34, 72, 190, 255))
        cover.paste((180, 38, 120, 255), (0, 0, 8, 8))
        cover.save(pages / "cover.png")
        for filename in REQUIRED_SLIDES:
            target = pages / filename
            if not target.exists():
                Image.new("RGBA", (18, 18), (80, 110, 150, 255)).save(
                    target
                )
        for filename in CONTROL_FILES:
            Image.new("RGBA", (12, 9), (180, 180, 180, 255)).save(
                controls / filename
            )

        banner = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        banner.paste((245, 245, 245, 255), (10, 45, 90, 62))
        banner.putpixel((10, 10), (220, 150, 20, 255))
        banner.save(root / APP_BANNER_PATH)
        return pages / "cover.png"

    def test_prepares_every_variant_and_invalidates_when_cover_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cover = self._write_sources(root)

            prepared = curtain_cache.prepare_curtain_variant_cache(root)

            self.assertIsNotNone(prepared)
            assert prepared is not None
            original_colors = dict(prepared.colors)
            self.assertEqual(
                set(prepared.colors),
                set(curtain_cache.CURTAIN_CACHE_STYLES),
            )
            for style in curtain_cache.CURTAIN_CACHE_STYLES:
                style_directory = prepared.style_directory(style)
                self.assertIsNotNone(style_directory)
                assert style_directory is not None
                for filename in curtain_cache.CURTAIN_CACHE_FILES:
                    self.assertTrue((style_directory / filename).is_file())

            with (
                mock.patch.object(
                    curtain_cache,
                    "write_tinted_curtain_image",
                ) as tint,
                mock.patch.object(
                    curtain_cache,
                    "write_recolored_banner_image",
                ) as recolor,
            ):
                reused = curtain_cache.prepare_curtain_variant_cache(root)
            self.assertIsNotNone(reused)
            assert reused is not None
            self.assertEqual(reused.directory, prepared.directory)
            tint.assert_not_called()
            recolor.assert_not_called()

            Image.new("RGBA", (18, 18), (28, 170, 72, 255)).save(cover)
            self.assertIsNone(curtain_cache.load_curtain_variant_cache(root))
            refreshed = curtain_cache.prepare_curtain_variant_cache(root)
            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertEqual(
                refreshed.colors[CURTAIN_STYLE_WHITE],
                original_colors[CURTAIN_STYLE_WHITE],
            )
            for style in curtain_cache.CURTAIN_DYNAMIC_STYLES:
                self.assertNotEqual(
                    refreshed.colors[style],
                    original_colors[style],
                )

    def test_cover_change_during_preparation_cannot_publish_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cover = self._write_sources(root)
            original_fingerprint = curtain_cache.curtain_source_fingerprint(root)
            original_writer = curtain_cache.write_tinted_curtain_image
            changed = False

            def change_cover_after_first_render(*args, **kwargs) -> None:
                nonlocal changed
                original_writer(*args, **kwargs)
                if not changed:
                    changed = True
                    Image.new("RGBA", (18, 18), (18, 170, 80, 255)).save(
                        cover
                    )

            with mock.patch.object(
                curtain_cache,
                "write_tinted_curtain_image",
                side_effect=change_cover_after_first_render,
            ):
                prepared = curtain_cache.prepare_curtain_variant_cache(root)

            self.assertIsNone(prepared)
            self.assertFalse(
                (
                    root
                    / curtain_cache.CURTAIN_CACHE_ROOT
                    / original_fingerprint
                ).exists()
            )

    def test_all_dynamic_colors_share_one_cover_analysis(self) -> None:
        with mock.patch.object(
            curtain_color,
            "_representative_cover_color",
            return_value=(42, 80, 170),
        ) as analyze:
            colors = curtain_color.curtain_variant_rgbs(Path("cover.png"))

        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(
            set(colors),
            set(curtain_cache.CURTAIN_CACHE_STYLES),
        )
        self.assertEqual(
            set(curtain_cache.CURTAIN_DYNAMIC_STYLES),
            {
                CURTAIN_STYLE_NORMAL,
                CURTAIN_STYLE_COMPLEMENTARY,
                CURTAIN_STYLE_NORMAL_LIGHT,
                CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
                CURTAIN_STYLE_NORMAL_DARK,
                CURTAIN_STYLE_COMPLEMENTARY_DARK,
            },
        )

    def test_display_palette_uses_all_seven_shared_color_relationships(self) -> None:
        variants = {
            CURTAIN_STYLE_WHITE: (255, 255, 255),
            CURTAIN_STYLE_NORMAL: (176, 75, 48),
            CURTAIN_STYLE_COMPLEMENTARY: (48, 149, 176),
            CURTAIN_STYLE_NORMAL_LIGHT: (234, 204, 196),
            CURTAIN_STYLE_COMPLEMENTARY_LIGHT: (196, 226, 234),
            CURTAIN_STYLE_NORMAL_DARK: (91, 27, 10),
            CURTAIN_STYLE_COMPLEMENTARY_DARK: (10, 74, 91),
        }

        palette = curtain_color.curtain_display_palette(variants)

        self.assertEqual(tuple(palette), tuple(variants))
        self.assertEqual(
            palette[CURTAIN_STYLE_WHITE],
            curtain_color.CurtainDisplayColors(
                background=(255, 255, 255),
                foreground=(0, 0, 0),
            ),
        )
        self.assertEqual(
            palette[CURTAIN_STYLE_NORMAL],
            curtain_color.CurtainDisplayColors(
                background=variants[CURTAIN_STYLE_NORMAL],
                foreground=variants[CURTAIN_STYLE_COMPLEMENTARY],
            ),
        )
        self.assertEqual(
            palette[CURTAIN_STYLE_COMPLEMENTARY],
            curtain_color.CurtainDisplayColors(
                background=variants[CURTAIN_STYLE_COMPLEMENTARY],
                foreground=variants[CURTAIN_STYLE_NORMAL],
            ),
        )
        for style in (
            CURTAIN_STYLE_NORMAL_LIGHT,
            CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
            CURTAIN_STYLE_NORMAL_DARK,
            CURTAIN_STYLE_COMPLEMENTARY_DARK,
        ):
            display = palette[style]
            self.assertEqual(display.background, variants[style])
            self.assertGreaterEqual(
                self._contrast_ratio(display.background, display.foreground),
                4.5,
            )

    def test_explicit_complementary_and_legacy_family_settings_normalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text(
                '{"curtain_style": "complementary_light"}',
                encoding="utf-8",
            )
            settings = SettingsStore(root)
            self.assertEqual(
                settings.get("curtain_style"),
                CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
            )
            self.assertEqual(
                settings.update_fields(
                    curtain_style="complementary_dark"
                )["curtain_style"],
                CURTAIN_STYLE_COMPLEMENTARY_DARK,
            )
            self.assertEqual(
                settings.update_fields(curtain_style="light")["curtain_style"],
                CURTAIN_STYLE_NORMAL_LIGHT,
            )
            self.assertEqual(
                settings.update_fields(curtain_style="dark")["curtain_style"],
                CURTAIN_STYLE_NORMAL_DARK,
            )

    def test_curtain_default_and_white_selection_remain_distinct(self) -> None:
        self.assertEqual(DEFAULT_CURTAIN_STYLE, "average_color")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root)
            self.assertEqual(settings.get("curtain_style"), DEFAULT_CURTAIN_STYLE)

            set_path_hidden(settings.path, False)
            settings.path.write_text(
                '{"starting_volume": 42}',
                encoding="utf-8",
            )
            self.assertEqual(settings.reload()["curtain_style"], DEFAULT_CURTAIN_STYLE)

            set_path_hidden(settings.path, False)
            settings.path.write_text(
                '{"curtain_style": "nonsense_value"}',
                encoding="utf-8",
            )
            self.assertEqual(settings.reload()["curtain_style"], DEFAULT_CURTAIN_STYLE)

            settings.update_fields(curtain_style="pure_white")
            self.assertEqual(settings.reload()["curtain_style"], "pure_white")

    def test_bundle_generation_copies_all_seven_prepared_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            settings = SettingsStore(root)
            settings.update_fields(recipient_name="Avery Stone")
            prepared = curtain_cache.prepare_curtain_variant_cache(root)
            self.assertIsNotNone(prepared)
            assert prepared is not None

            output_signatures: set[tuple[bytes, ...]] = set()
            with (
                mock.patch.object(
                    generate,
                    "write_tinted_curtain_image",
                    side_effect=AssertionError("curtain was regenerated"),
                ),
                mock.patch.object(
                    generate,
                    "write_recolored_banner_image",
                    side_effect=AssertionError("banner was regenerated"),
                ),
            ):
                for index, (style, label) in enumerate(CURTAIN_STYLE_OPTIONS):
                    settings.update_fields(
                        recipient_title=f"{index} {label}",
                        curtain_style=style,
                    )
                    play = generate.generate_play_bundle(
                        root,
                        message_html="",
                        seed_sfx=False,
                    )
                    cached_style = prepared.style_directory(style)
                    self.assertIsNotNone(cached_style)
                    assert cached_style is not None
                    signature = []
                    for filename in curtain_cache.CURTAIN_CACHE_FILES:
                        output_bytes = (
                            play / "gallery" / "controls" / filename
                        ).read_bytes()
                        self.assertEqual(
                            output_bytes,
                            (cached_style / filename).read_bytes(),
                        )
                        signature.append(output_bytes)
                    output_signatures.add(tuple(signature))

            self.assertEqual(
                len(output_signatures),
                len(CURTAIN_STYLE_OPTIONS),
            )

    def test_all_seven_saved_curtain_modes_round_trip_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            settings = SettingsStore(root)
            settings.update_fields(
                recipient_name="Avery Stone",
            )
            restorer = SavedLetterRestorer(root)
            for index, (style, label) in enumerate(CURTAIN_STYLE_OPTIONS):
                settings.update_fields(
                    recipient_title=f"{index} {label} Round Trip",
                    curtain_style=style,
                )
                saved = generate.generate_play_bundle(
                    root,
                    message_html=f"<p>{label}</p>",
                    seed_sfx=False,
                )
                update_saved_metadata(
                    saved,
                    root,
                    ReadinessResult((), 100, "Ready"),
                )
                metadata = json.loads(
                    (saved / PLAY_METADATA_FILE).read_text(encoding="utf-8")
                )
                self.assertEqual(metadata["settings"]["curtain_style"], style)
                replacement = (
                    CURTAIN_STYLE_WHITE
                    if style != CURTAIN_STYLE_WHITE
                    else CURTAIN_STYLE_COMPLEMENTARY
                )
                settings.update_fields(curtain_style=replacement)
                catalog = SavedLetterCatalog(root)
                catalog.list_entries()
                entries = catalog.refresh_entry(saved)
                self.assertIsNotNone(entries)
                assert entries is not None
                entry = next(
                    item for item in entries if item.path == saved.resolve()
                )
                restorer.restore(entry)
                self.assertEqual(settings.get("curtain_style"), style)

    def test_saved_curtain_missing_invalid_and_legacy_values_normalize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            settings = SettingsStore(root)
            settings.update_fields(
                recipient_name="Avery Stone",
                recipient_title="Legacy Curtains",
                curtain_style=CURTAIN_STYLE_NORMAL,
            )
            saved = generate.generate_play_bundle(
                root,
                message_html="<p>Legacy curtain</p>",
                seed_sfx=False,
            )
            update_saved_metadata(
                saved,
                root,
                ReadinessResult((), 100, "Ready"),
            )
            metadata_path = saved / PLAY_METADATA_FILE
            base_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            cases = (
                (None, CURTAIN_STYLE_NORMAL),
                ("nonsense_value", CURTAIN_STYLE_NORMAL),
                (
                    "complementary_light",
                    CURTAIN_STYLE_COMPLEMENTARY_LIGHT,
                ),
                (
                    "complementary_dark",
                    CURTAIN_STYLE_COMPLEMENTARY_DARK,
                ),
            )

            for stored_style, expected_style in cases:
                metadata = json.loads(json.dumps(base_metadata))
                if stored_style is None:
                    metadata["settings"].pop("curtain_style", None)
                else:
                    metadata["settings"]["curtain_style"] = stored_style
                set_path_hidden(metadata_path, False)
                metadata_path.write_text(
                    json.dumps(metadata, indent=2),
                    encoding="utf-8",
                )
                settings.update_fields(curtain_style=CURTAIN_STYLE_WHITE)
                entry = next(
                    item
                    for item in SavedLetterCatalog(root).list_entries()
                    if item.path == saved.resolve()
                )
                SavedLetterRestorer(root).restore(entry)
                self.assertEqual(
                    settings.get("curtain_style"),
                    expected_style,
                )


if __name__ == "__main__":
    unittest.main()
