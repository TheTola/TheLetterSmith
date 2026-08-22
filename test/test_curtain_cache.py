from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from config import APP_BANNER_PATH, CONTROL_FILES, REQUIRED_SLIDES
import curtain_cache
import curtain_color
import generate
from readiness import ReadinessResult
from saved_letters import (
    SavedLetterCatalog,
    SavedLetterRestorer,
    update_saved_metadata,
)
from settings_store import SettingsStore


class CurtainCacheTests(unittest.TestCase):
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
                "average_color",
                "complementary_average_color",
                "normal_light",
                "complementary_light",
                "normal_dark",
                "complementary_dark",
            },
        )

    def test_legacy_light_and_dark_settings_migrate_to_normal_variants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.json").write_text(
                '{"curtain_style": "light"}',
                encoding="utf-8",
            )
            settings = SettingsStore(root)
            self.assertEqual(settings.get("curtain_style"), "normal_light")
            self.assertEqual(
                settings.update_fields(curtain_style="dark")["curtain_style"],
                "normal_dark",
            )

    def test_bundle_generation_copies_prepared_variant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_sources(root)
            SettingsStore(root).update_fields(
                curtain_style="complementary_dark"
            )
            prepared = curtain_cache.prepare_curtain_variant_cache(root)
            self.assertIsNotNone(prepared)
            assert prepared is not None
            cached_style = prepared.style_directory("complementary_dark")
            self.assertIsNotNone(cached_style)
            assert cached_style is not None

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
                play = generate.generate_play_bundle(
                    root,
                    message_html="",
                    seed_sfx=False,
                )

            for filename in curtain_cache.CURTAIN_CACHE_FILES:
                self.assertEqual(
                    (play / "gallery" / "controls" / filename).read_bytes(),
                    (cached_style / filename).read_bytes(),
                )

    def test_saved_letter_restore_rebuilds_its_curtain_banner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cover = self._write_sources(root)
            settings = SettingsStore(root)
            settings.update_fields(
                recipient_name="Avery Stone",
                recipient_title="Blue Evening",
                curtain_style="complementary_dark",
            )
            saved_cover = cover.read_bytes()
            saved = generate.generate_play_bundle(
                root,
                message_html="<p>Saved curtain</p>",
                seed_sfx=False,
            )
            update_saved_metadata(
                saved,
                root,
                ReadinessResult((), 100, "Ready"),
            )
            saved_banner = (
                saved / "gallery" / "controls" / "bannerman.png"
            ).read_bytes()

            Image.new("RGBA", (18, 18), (28, 170, 72, 255)).save(cover)
            settings.update_fields(curtain_style="pure_white")
            entry = next(
                item
                for item in SavedLetterCatalog(root).list_entries()
                if item.path == saved.resolve()
            )
            SavedLetterRestorer(root).restore(entry)

            self.assertEqual(cover.read_bytes(), saved_cover)
            self.assertEqual(
                settings.get("curtain_style"),
                "complementary_dark",
            )
            restored_cache = curtain_cache.prepare_curtain_variant_cache(root)
            self.assertIsNotNone(restored_cache)
            preview = saved.with_name("restored-preview")
            generate.build_play_bundle_to(
                root,
                preview,
                message_html="<p>Restored curtain</p>",
                seed_sfx=False,
            )
            self.assertEqual(
                (
                    preview / "gallery" / "controls" / "bannerman.png"
                ).read_bytes(),
                saved_banner,
            )


if __name__ == "__main__":
    unittest.main()
