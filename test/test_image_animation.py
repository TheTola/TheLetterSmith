from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from PIL import Image

from config import CONTROL_FILES
import generate
import image_animation
from image_animation import (
    IMAGE_MANIFEST_NAME,
    build_runtime_image_assets,
    inspect_gif,
    install_image_asset,
    load_image_manifest,
    reconcile_external_image_assets,
    update_slot_gif_settings,
    validate_runtime_image_manifest,
)
from portable_export import create_single_html
from settings_store import SettingsStore


class ImageAnimationTests(unittest.TestCase):
    @staticmethod
    def _write_gif(
        path: Path,
        *,
        durations: tuple[int, ...] = (80, 120, 160),
        loop: int | None = 0,
        size: tuple[int, int] = (12, 9),
    ) -> None:
        frames = [
            Image.new("RGBA", size, color)
            for color in ("red", "green", "blue")[: len(durations)]
        ]
        options = {
            "format": "GIF",
            "save_all": True,
            "append_images": frames[1:],
            "duration": list(durations),
            "disposal": 2,
        }
        if loop is not None:
            options["loop"] = loop
        frames[0].save(path, **options)

    @staticmethod
    def _write_png(path: Path, color: str = "white") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (12, 9), color).save(path, format="PNG")

    def test_install_preserves_original_gif_and_independent_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "pages"
            source = root / "source.gif"
            self._write_gif(source)
            original = source.read_bytes()

            record = install_image_asset(pages, "cover", source)
            settings = update_slot_gif_settings(
                pages,
                "cover",
                {
                    "playback_mode": "loop",
                    "play_count": 17,
                    "start_delay_ms": 1500,
                    "loop_pause_ms": 4000,
                },
            )
            manifest = load_image_manifest(pages)

            self.assertEqual(record["asset_type"], "animated_gif")
            self.assertEqual((pages / "cover.gif").read_bytes(), original)
            self.assertTrue((pages / "cover.png").is_file())
            self.assertEqual(manifest["slots"]["cover"]["settings"], settings)
            self.assertNotIn("animation_speed", json.dumps(manifest))

    def test_original_loop_metadata_and_static_gif_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            non_looping = root / "once.gif"
            finite = root / "finite.gif"
            single = root / "single.gif"
            self._write_gif(non_looping, loop=None)
            self._write_gif(finite, loop=2)
            Image.new("RGB", (8, 8), "purple").save(single, format="GIF")

            self.assertEqual(inspect_gif(non_looping).embedded_play_count, 1)
            self.assertEqual(inspect_gif(finite).embedded_play_count, 3)
            record = install_image_asset(root / "pages", "letter", single)
            self.assertEqual(record["asset_type"], "static")
            self.assertFalse((root / "pages" / "letter.gif").exists())

    def test_direct_gif_replacement_repairs_static_manifest_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory) / "pages"
            pages.mkdir()
            self._write_png(pages / "cover.png", "black")
            install_image_asset(pages, "cover", pages / "cover.png")
            (pages / "cover.png").unlink()
            self._write_gif(pages / "cover.gif")

            self.assertTrue(reconcile_external_image_assets(pages))

            record = load_image_manifest(pages)["slots"]["cover"]
            self.assertEqual(record["asset_type"], "animated_gif")
            self.assertEqual(record["gif"]["frame_count"], 3)
            self.assertTrue((pages / "cover.png").is_file())
            self.assertFalse(reconcile_external_image_assets(pages))

    def test_direct_single_frame_gif_is_normalized_to_static_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory) / "pages"
            pages.mkdir()
            gif_path = pages / "letter.gif"
            Image.new("RGB", (8, 8), "purple").save(gif_path, format="GIF")

            self.assertTrue(reconcile_external_image_assets(pages))

            record = load_image_manifest(pages)["slots"]["letter"]
            self.assertEqual(record["asset_type"], "static")
            self.assertTrue((pages / "letter.png").is_file())
            self.assertFalse(gif_path.exists())

    def test_generated_gallery_controls_frames_and_survives_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "gallery/user/pages"
            controls = root / "gallery/user/card/controls"
            pages.mkdir(parents=True)
            controls.mkdir(parents=True)

            gif_sources: dict[str, Path] = {}
            for slot in ("cover", "letter", "wall", "back"):
                source = root / f"{slot}-source.gif"
                self._write_gif(source)
                gif_sources[slot] = source
                install_image_asset(pages, slot, source)

            expected_settings = {
                "cover": {
                    "playback_mode": "loop",
                    "play_count": "forever",
                    "start_delay_ms": 0,
                    "loop_pause_ms": 2000,
                },
                "letter": {
                    "playback_mode": "loop",
                    "play_count": 3,
                    "start_delay_ms": 1500,
                    "loop_pause_ms": 4000,
                },
                "wall": {
                    "playback_mode": "ping_pong",
                    "play_count": "forever",
                    "start_delay_ms": 3000,
                    "loop_pause_ms": 1000,
                },
                "back": {
                    "playback_mode": "original",
                    "play_count": 1,
                    "start_delay_ms": 0,
                    "loop_pause_ms": 0,
                },
            }
            for slot, settings in expected_settings.items():
                update_slot_gif_settings(pages, slot, settings)

            for filename in CONTROL_FILES:
                self._write_png(controls / filename)
            SettingsStore(root).update_fields(
                {
                    "recipient_name": "Amanda Miller",
                    "recipient_title": "Perfection's Path",
                }
            )

            play = generate.generate_play_bundle(
                str(root),
                message_html="<p>Animated letter</p>",
                seed_sfx=False,
            )
            generate.validate_play_bundle(play)
            runtime_manifest = validate_runtime_image_manifest(
                play / "gallery/pages"
            )
            index = (play / "index.html").read_text(encoding="utf-8")
            script = (play / "script.js").read_text(encoding="utf-8")
            styles = (play / "styles.css").read_text(encoding="utf-8")

            for slot, settings in expected_settings.items():
                record = runtime_manifest["slots"][slot]
                self.assertEqual(record["settings"], settings)
                self.assertEqual(
                    (play / "gallery/pages" / f"{slot}.gif").read_bytes(),
                    gif_sources[slot].read_bytes(),
                )
                self.assertEqual(len(record["gif"]["frames"]), 3)
                self.assertEqual(record["gif"]["durations_ms"], [80, 120, 160])

            self.assertNotIn("{{IMAGE_ANIMATIONS_JSON}}", index)
            self.assertNotIn("{{TITLE_BANNER_RGB}}", index)
            self.assertNotIn("{{TITLE_BANNER_TEXT_RGB}}", index)
            self.assertIn('id="title-banner"', index)
            self.assertIn("Perfection&#x27;s Path", index)
            self.assertIn("#title-banner.is-showing", styles)
            self.assertIn(
                "const titleBannerDelayMs = prefersReducedMotion ? 80 : 500;",
                script,
            )
            self.assertIn(
                "const titleBannerHoldMs = 3500;",
                script,
            )
            self.assertIn("beginBtn.classList.add('is-dismissed')", script)
            self.assertIn("dismissTitleBanner();\n    flipTo(target);", script)
            self.assertIn("pointer-events:none", styles)
            self.assertIn('"playback_mode": "ping_pong"', index)
            self.assertIn("playImageReverse", script)
            self.assertIn("completed animation stays on its last displayed frame", script)
            self.assertNotIn("animation_speed", index + script)

            restored_pages = root / "restored-pages"
            shutil.copytree(play / "gallery/pages", restored_pages)
            restored_manifest = load_image_manifest(restored_pages)
            self.assertEqual(
                restored_manifest["slots"]["letter"]["settings"],
                expected_settings["letter"],
            )

            exported = create_single_html(play, root / "exports")
            exported_html = exported.read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", exported_html)
            self.assertNotIn("gallery/pages/gif_frames/cover", exported_html)

    def test_runtime_builder_keeps_static_paths_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            for slot in ("cover", "letter", "wall", "back"):
                self._write_png(source / f"{slot}.png")

            runtime = build_runtime_image_assets(source, destination)

            self.assertEqual(runtime.animations, {})
            self.assertEqual(
                runtime.page_sources["cover"],
                "gallery/pages/cover.png",
            )
            self.assertTrue((destination / IMAGE_MANIFEST_NAME).is_file())

    def test_default_gif_playback_uses_native_file_without_extracted_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            gif = root / "source.gif"
            self._write_gif(gif)
            for slot in ("cover", "letter", "wall", "back"):
                install_image_asset(source, slot, gif)

            runtime = build_runtime_image_assets(source, destination)
            manifest = validate_runtime_image_manifest(destination)

            self.assertFalse((destination / "gif_frames").exists())
            self.assertEqual(
                runtime.page_sources["cover"],
                "gallery/pages/cover.png",
            )
            self.assertEqual(
                runtime.animations["0"]["source"],
                "gallery/pages/cover.gif",
            )
            for record in manifest["slots"].values():
                self.assertEqual(record["gif"]["render_mode"], "native_gif")
                self.assertNotIn("frames", record["gif"])

    def test_large_gif_is_normalized_for_viewer_and_thumbnail_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "large.gif"
            pages = root / "pages"
            self._write_gif(
                source,
                durations=(80, 120),
                size=(2100, 1050),
            )

            with mock.patch(
                "image_animation._normalize_animated_gif",
                wraps=image_animation._normalize_animated_gif,
            ) as normalize:
                record = install_image_asset(pages, "cover", source)
                reused = install_image_asset(pages, "letter", source)

            self.assertEqual(normalize.call_count, 1)
            self.assertEqual(
                (pages / "cover.gif").read_bytes(),
                (pages / "letter.gif").read_bytes(),
            )
            self.assertEqual(
                record["import_source_sha256"],
                reused["import_source_sha256"],
            )

            with Image.open(pages / "cover.gif") as processed:
                self.assertEqual(processed.size, (2048, 1024))
                self.assertEqual(processed.n_frames, 2)
            with Image.open(pages / "cover.png") as preview:
                self.assertEqual(preview.size, (2048, 1024))
            with Image.open(pages / "cover.thumbnail.gif") as thumbnail:
                self.assertEqual(thumbnail.size, (384, 192))
                self.assertEqual(thumbnail.n_frames, 2)
            self.assertEqual(record["processing"]["max_dimension"], 2048)
            self.assertEqual(record["thumbnail_file"], "cover.thumbnail.gif")


if __name__ == "__main__":
    unittest.main()
