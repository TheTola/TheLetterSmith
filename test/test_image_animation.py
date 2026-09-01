from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

from config import CONTROL_FILES
from curtain_color import recolor_banner_to_curtain_color
import generate
import image_animation
from Image_tab import (
    ImageAssetCard,
    ImageSettingsDialog,
    ImageTab,
    StockImageDialog,
    _PreparedImageImportResult,
    _ImageThumbnail,
    _ResetImagesConfirmationDialog,
)
from image_animation import (
    IMAGE_MANIFEST_NAME,
    build_runtime_image_assets,
    inspect_gif,
    install_image_asset,
    load_image_manifest,
    normalize_gif_settings,
    prepare_image_asset_import,
    reconcile_external_image_assets,
    update_slot_gif_settings,
    validate_runtime_image_manifest,
    write_image_manifest,
)
from portable_export import create_single_html
from Message_tab import MessageTab
from project_state import ProjectStateController
from settings_store import SettingsStore
from ui_sounds import UiSound
from ui_theme import BUTTON_TIER_STYLES, THEMES, ButtonTier, ThemeService


class _FakeMovie:
    def __init__(self, state: QtGui.QMovie.MovieState) -> None:
        self._state = state
        self.start_calls = 0

    def state(self) -> QtGui.QMovie.MovieState:
        return self._state

    def setPaused(self, paused: bool) -> None:
        self._state = (
            QtGui.QMovie.Paused
            if paused
            else QtGui.QMovie.Running
        )

    def start(self) -> None:
        self.start_calls += 1
        self._state = QtGui.QMovie.Running


class ImageAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

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

    @staticmethod
    def _write_banner_source(root: Path) -> Path:
        path = root / generate.APP_BANNER_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        image.putpixel((10, 10), (220, 150, 20, 255))
        image.putpixel((20, 50), (248, 244, 230, 255))
        image.putpixel((80, 75), (150, 150, 155, 255))
        image.putpixel((50, 38), (150, 150, 155, 255))
        image.putpixel((50, 39), (255, 255, 255, 255))
        image.save(path, format="PNG")
        return path

    def test_banner_recolor_preserves_gold_alpha_and_category_shading(self) -> None:
        source = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        source.putpixel((10, 10), (220, 150, 20, 255))
        source.putpixel((20, 50), (248, 244, 230, 255))
        source.putpixel((80, 75), (150, 150, 155, 255))
        source.putpixel((50, 38), (150, 150, 155, 255))
        source.putpixel((50, 39), (255, 255, 255, 255))
        source.putpixel((50, 50), (247, 247, 247, 255))
        source.putpixel((1, 1), (255, 255, 255, 3))
        source.putpixel((2, 1), (255, 255, 255, 252))

        themed = recolor_banner_to_curtain_color(source, (48, 52, 176))

        self.assertEqual(themed.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(themed.getpixel((1, 1))[3], 0)
        self.assertEqual(themed.getpixel((2, 1))[3], 255)
        self.assertEqual(themed.getpixel((10, 10)), source.getpixel((10, 10)))
        self.assertEqual(themed.getpixel((20, 50)), (48, 52, 176, 255))
        neutral_parchment = themed.getpixel((50, 50))
        self.assertNotEqual(neutral_parchment, source.getpixel((50, 50)))
        self.assertGreater(neutral_parchment[2], neutral_parchment[0])
        ribbon = themed.getpixel((80, 75))
        gem = themed.getpixel((50, 38))
        self.assertGreater(ribbon[2], ribbon[0])
        self.assertLess(sum(ribbon[:3]), sum(themed.getpixel((20, 50))[:3]))
        self.assertGreater(gem[2], gem[0])
        self.assertGreater(sum(gem[:3]), sum(themed.getpixel((20, 50))[:3]))
        self.assertEqual(themed.getpixel((50, 39)), (255, 255, 255, 255))

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
                    "animation_enabled": False,
                    "speed_percent": 175,
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
            self.assertFalse(settings["animation_enabled"])
            self.assertEqual(settings["speed_percent"], 175)
            if os.name == "nt":
                self.assertTrue(
                    (pages / IMAGE_MANIFEST_NAME).stat().st_file_attributes
                    & stat.FILE_ATTRIBUTE_HIDDEN
                )

    def test_gif_settings_normalize_enablement_and_speed_bounds(self) -> None:
        self.assertEqual(
            normalize_gif_settings(None)["animation_enabled"],
            True,
        )
        self.assertEqual(normalize_gif_settings(None)["speed_percent"], 100)
        self.assertFalse(
            normalize_gif_settings({"animation_enabled": "off"})[
                "animation_enabled"
            ]
        )
        self.assertEqual(
            normalize_gif_settings({"speed_percent": 5})["speed_percent"],
            25,
        )
        self.assertEqual(
            normalize_gif_settings({"speed_percent": 900})["speed_percent"],
            400,
        )
        self.assertEqual(
            normalize_gif_settings({"speed_percent": "invalid"})[
                "speed_percent"
            ],
            100,
        )

    def test_gif_settings_dialog_round_trips_start_stop_and_speed(self) -> None:
        dialog = ImageSettingsDialog(
            "Cover Page",
            animated_gif=True,
            settings={
                "animation_enabled": False,
                "speed_percent": 175,
            },
        )
        self.addCleanup(dialog.deleteLater)

        self.assertFalse(dialog.animation_enabled.isChecked())
        self.assertEqual(dialog.speed_percent.value(), 175)
        dialog.animation_enabled.setChecked(True)
        dialog.speed_percent.setValue(225)

        settings = dialog.gif_settings()
        self.assertTrue(settings["animation_enabled"])
        self.assertEqual(settings["speed_percent"], 225)

    def test_prepared_import_changes_workspace_and_project_only_on_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "gallery/user/pages"
            project_pages = root / "saved/pages"
            source = root / "replacement.png"
            self._write_png(source, "green")
            self._write_png(pages / "cover.png", "red")
            project_pages.mkdir(parents=True)
            shutil.copy2(pages / "cover.png", project_pages / "cover.png")

            prepared = prepare_image_asset_import(
                pages,
                "cover",
                source,
                project_pages_directory=project_pages,
            )
            try:
                with Image.open(pages / "cover.png") as workspace_image:
                    self.assertEqual(workspace_image.getpixel((0, 0))[:3], (255, 0, 0))
                with Image.open(project_pages / "cover.png") as project_image:
                    self.assertEqual(project_image.getpixel((0, 0))[:3], (255, 0, 0))

                prepared.commit()
                if os.name == "nt":
                    self.assertTrue(
                        (pages / IMAGE_MANIFEST_NAME).stat().st_file_attributes
                        & stat.FILE_ATTRIBUTE_HIDDEN
                    )
                    self.assertTrue(
                        (project_pages / IMAGE_MANIFEST_NAME).stat().st_file_attributes
                        & stat.FILE_ATTRIBUTE_HIDDEN
                    )
                with Image.open(pages / "cover.png") as workspace_image:
                    self.assertEqual(workspace_image.getpixel((0, 0))[:3], (0, 128, 0))
                with Image.open(project_pages / "cover.png") as project_image:
                    self.assertEqual(project_image.getpixel((0, 0))[:3], (0, 128, 0))

                prepared.rollback()
                with Image.open(pages / "cover.png") as workspace_image:
                    self.assertEqual(workspace_image.getpixel((0, 0))[:3], (255, 0, 0))
                with Image.open(project_pages / "cover.png") as project_image:
                    self.assertEqual(project_image.getpixel((0, 0))[:3], (255, 0, 0))
            finally:
                prepared.abort()

    def test_prepared_import_rolls_back_an_interrupted_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "pages"
            source = root / "replacement.png"
            self._write_png(source, "green")
            self._write_png(pages / "cover.png", "red")
            prepared = prepare_image_asset_import(pages, "cover", source)
            failing_transaction = prepared._changes[1].transaction
            try:
                with (
                    mock.patch.object(
                        failing_transaction,
                        "commit",
                        side_effect=OSError("locked"),
                    ),
                    self.assertRaises(OSError),
                ):
                    prepared.commit()
                with Image.open(pages / "cover.png") as restored:
                    self.assertEqual(restored.getpixel((0, 0))[:3], (255, 0, 0))
            finally:
                prepared.abort()

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
                    "animation_enabled": True,
                    "speed_percent": 100,
                    "playback_mode": "loop",
                    "play_count": "forever",
                    "start_delay_ms": 0,
                    "loop_pause_ms": 2000,
                },
                "letter": {
                    "animation_enabled": True,
                    "speed_percent": 50,
                    "playback_mode": "loop",
                    "play_count": 3,
                    "start_delay_ms": 1500,
                    "loop_pause_ms": 4000,
                },
                "wall": {
                    "animation_enabled": False,
                    "speed_percent": 125,
                    "playback_mode": "ping_pong",
                    "play_count": "forever",
                    "start_delay_ms": 3000,
                    "loop_pause_ms": 1000,
                },
                "back": {
                    "animation_enabled": True,
                    "speed_percent": 200,
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
            self._write_banner_source(root)
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
            self.assertIn('id="title-banner-art"', index)
            self.assertIn("gallery/controls/bannerman.png", index)
            self.assertTrue(
                (play / "gallery/controls/bannerman.png").is_file()
            )
            self.assertIn("Perfection&#x27;s Path", index)
            self.assertIn("#title-banner.is-showing", styles)
            self.assertIn("container-type:inline-size", styles)
            self.assertIn("font-size:6.8cqw", styles)
            self.assertNotIn("font-size:clamp(24px,4.7vw,68px)", styles)
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
            self.assertIn("--duration-overlay:180ms", styles)
            self.assertIn("--duration-curtain:1500ms", styles)
            self.assertIn(".text-wall.is-open{opacity:1", styles)
            self.assertIn("#turnShadow{position:absolute", styles)
            self.assertIn("#slideshow.page-turning .nav-button", styles)
            self.assertIn("const goingNext = target > idx", script)
            self.assertIn("const eased = motion.ease(raw)", script)
            self.assertIn("if (!started || flipping", script)
            self.assertIn('"playback_mode": "ping_pong"', index)
            self.assertIn('"animation_enabled": false', index)
            self.assertIn('"speed_percent": 50', index)
            self.assertIn("playImageReverse", script)
            self.assertIn("authoredDuration * 100 / speedPercent", script)
            self.assertIn("config.animation_enabled === false", script)
            self.assertIn("completed animation stays on its last displayed frame", script)

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
            self.assertNotIn("gallery/controls/bannerman.png", exported_html)
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

            update_slot_gif_settings(
                source,
                "cover",
                {"speed_percent": 200},
            )
            scaled_destination = root / "scaled-destination"
            scaled = build_runtime_image_assets(source, scaled_destination)
            self.assertEqual(scaled.animations["0"]["render_mode"], "frames")
            self.assertEqual(scaled.animations["0"]["speed_percent"], 200)

    def test_runtime_validator_accepts_legacy_default_animation_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            gif = root / "source.gif"
            self._write_gif(gif)
            install_image_asset(source, "cover", gif)
            for slot in ("letter", "wall", "back"):
                self._write_png(source / f"{slot}.png")

            build_runtime_image_assets(source, destination)
            manifest_path = destination / IMAGE_MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            settings = manifest["slots"]["cover"]["settings"]
            settings.pop("animation_enabled")
            settings.pop("speed_percent")
            write_image_manifest(destination, manifest)

            validated = validate_runtime_image_manifest(destination)

            self.assertTrue(
                validated["slots"]["cover"]["settings"]["animation_enabled"]
            )
            self.assertEqual(
                validated["slots"]["cover"]["settings"]["speed_percent"],
                100,
            )

    def test_runtime_builder_reuses_unchanged_custom_gif_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            first = root / "first"
            second = root / "second"
            gif = root / "source.gif"
            self._write_gif(gif)
            install_image_asset(source, "cover", gif)
            update_slot_gif_settings(
                source,
                "cover",
                {"playback_mode": "ping_pong", "play_count": 2},
            )
            for slot in ("letter", "wall", "back"):
                self._write_png(source / f"{slot}.png")

            original = build_runtime_image_assets(source, first)
            with mock.patch.object(
                image_animation,
                "_extract_gif_frames",
                side_effect=AssertionError("unchanged frames were regenerated"),
            ):
                reused = build_runtime_image_assets(
                    source,
                    second,
                    reuse_pages_directory=first,
                )

            self.assertEqual(
                original.animations["0"]["frames"],
                reused.animations["0"]["frames"],
            )
            validate_runtime_image_manifest(second)

    def test_runtime_builder_regenerates_corrupt_cached_gif_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            first = root / "first"
            second = root / "second"
            gif = root / "source.gif"
            self._write_gif(gif)
            install_image_asset(source, "cover", gif)
            update_slot_gif_settings(
                source,
                "cover",
                {"playback_mode": "ping_pong", "play_count": 2},
            )
            for slot in ("letter", "wall", "back"):
                self._write_png(source / f"{slot}.png")

            original = build_runtime_image_assets(source, first)
            cached_frame = first / original.manifest["slots"]["cover"]["gif"][
                "frames"
            ][0]
            cached_frame.write_bytes(b"broken")
            with mock.patch.object(
                image_animation,
                "_extract_gif_frames",
                wraps=image_animation._extract_gif_frames,
            ) as extract:
                rebuilt = build_runtime_image_assets(
                    source,
                    second,
                    reuse_pages_directory=first,
                )

            extract.assert_called_once()
            validate_runtime_image_manifest(second)
            regenerated = second / rebuilt.manifest["slots"]["cover"]["gif"][
                "frames"
            ][0]
            with Image.open(regenerated) as frame:
                frame.verify()

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

    def test_stock_image_tray_is_compact_frameless_and_single_click(self) -> None:
        paths = tuple(Path(f"stock-{index}.png") for index in range(3))
        dialog = StockImageDialog("Cover Page", paths)
        try:
            self.assertTrue(dialog.windowFlags() & QtCore.Qt.Popup)
            self.assertTrue(dialog.windowFlags() & QtCore.Qt.FramelessWindowHint)
            self.assertEqual(dialog.size(), QtCore.QSize(420, 146))
            self.assertIsNone(dialog.findChild(QtWidgets.QDialogButtonBox))

            item = dialog._images.item(1)
            dialog._images.itemClicked.emit(item)

            self.assertEqual(dialog.result(), QtWidgets.QDialog.Accepted)
            self.assertEqual(dialog.selected_path(), paths[1])
        finally:
            dialog.close()

    def test_empty_image_uses_stock_tray_and_filled_image_browses(self) -> None:
        tab = mock.Mock()
        tab.labels = {1: ("Cover Page", "cover.png")}
        tab.image_paths = {1: None}

        ImageTab._pick_image_dialog(tab, 1)

        tab.open_stock_gallery.assert_called_once_with(1)
        tab._browse_image_file.assert_not_called()

        tab.image_paths[1] = "cover.png"
        ImageTab._pick_image_dialog(tab, 1)

        tab._browse_image_file.assert_called_once_with(1)

    def test_image_and_message_imports_open_in_downloads(self) -> None:
        image_tab = mock.Mock()
        image_tab.labels = {1: ("Cover Page", "cover.png")}
        message_tab = mock.Mock()
        with (
            mock.patch(
                "Image_tab.QtCore.QStandardPaths.writableLocation",
                return_value="C:/Users/Test/Downloads",
            ),
            mock.patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                return_value=("", ""),
            ) as image_chooser,
        ):
            ImageTab._browse_image_file(image_tab, 1)

        with (
            mock.patch(
                "Message_tab.QtCore.QStandardPaths.writableLocation",
                return_value="C:/Users/Test/Downloads",
            ),
            mock.patch.object(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                return_value=("", ""),
            ) as message_chooser,
        ):
            MessageTab.select_file(message_tab)

        self.assertEqual(image_chooser.call_args.args[2], "C:/Users/Test/Downloads")
        self.assertIn("*.gif", image_chooser.call_args.args[3])
        self.assertEqual(message_chooser.call_args.args[2], "C:/Users/Test/Downloads")

    def test_saved_gif_settings_refresh_preview_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pages = Path(directory) / "pages"
            source = Path(directory) / "source.gif"
            self._write_gif(source)
            install_image_asset(pages, "cover", source)
            dialog = mock.Mock()
            dialog.exec.return_value = QtWidgets.QDialog.Accepted
            dialog.gif_settings.return_value = {
                "animation_enabled": True,
                "speed_percent": 225,
            }
            tab = SimpleNamespace(
                labels={1: ("Cover Page", "cover.png")},
                _user_pages_dir=mock.Mock(return_value=str(pages)),
                project_save_service=SimpleNamespace(
                    copy_workspace_file=mock.Mock()
                ),
                refresh_cards=mock.Mock(),
                _commit_image_change=mock.Mock(),
                animation_settings_changed=SimpleNamespace(emit=mock.Mock()),
                _show_temporary_status=mock.Mock(),
            )

            with mock.patch(
                "Image_tab.ImageSettingsDialog",
                return_value=dialog,
            ):
                ImageTab.open_image_settings(tab, 1)

            tab.refresh_cards.assert_called_once_with()
            saved = load_image_manifest(pages)["slots"]["cover"]["settings"]
            self.assertTrue(saved["animation_enabled"])
            self.assertEqual(saved["speed_percent"], 225)

    def test_reset_confirmation_is_frameless_modal_and_color_coded(self) -> None:
        dialog = _ResetImagesConfirmationDialog()
        try:
            self.assertTrue(
                dialog.windowFlags()
                & QtCore.Qt.FramelessWindowHint
            )
            self.assertTrue(dialog.isModal())
            self.assertEqual(
                dialog.windowModality(),
                QtCore.Qt.ApplicationModal,
            )
            self.assertEqual(dialog.yes_button.text(), "Yes")
            self.assertEqual(dialog.no_button.text(), "No")
            self.assertEqual(dialog.yes_button.size(), dialog.no_button.size())
            self.assertEqual(dialog.yes_button.height(), 42)
            self.assertIn("#ff626c", dialog.styleSheet())
            self.assertIn("#00d0ff", dialog.styleSheet())
        finally:
            dialog.close()

    def test_thumbnail_frame_uses_each_theme_secondary_color(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for theme_id in THEMES:
                frame = (
                    root
                    / "resources"
                    / "app"
                    / "themes"
                    / theme_id
                    / "image_frame.png"
                )
                frame.parent.mkdir(parents=True, exist_ok=True)
                image = QtGui.QImage(4, 4, QtGui.QImage.Format_ARGB32)
                image.fill(QtCore.Qt.transparent)
                image.setPixelColor(0, 0, QtGui.QColor("#00d0ff"))
                self.assertTrue(image.save(str(frame)))

            thumbnail = _ImageThumbnail()
            self.addCleanup(thumbnail.deleteLater)
            service = ThemeService(root, parent=thumbnail)
            frame_colors = set()
            for theme_id, definition in THEMES.items():
                service.set_theme(theme_id, persist=False)
                thumbnail.apply_theme_assets(service, root)
                expected = QtGui.QColor(definition.tokens.secondary)
                self.assertEqual(thumbnail.theme_frame_color, expected)
                self.assertEqual(
                    thumbnail._theme_frame.toImage().pixelColor(0, 0),
                    expected,
                )
                frame_colors.add(expected.name())

            self.assertEqual(len(frame_colors), len(THEMES))

    def test_reset_requires_yes_before_clearing_images(self) -> None:
        tab = mock.Mock()
        with mock.patch(
            "Image_tab._ResetImagesConfirmationDialog"
        ) as dialog_type:
            dialog_type.return_value.exec.return_value = (
                QtWidgets.QDialog.DialogCode.Rejected
            )
            ImageTab.reset_images(tab)
            tab._reset_images_confirmed.assert_not_called()

            dialog_type.return_value.exec.return_value = (
                QtWidgets.QDialog.DialogCode.Accepted
            )
            ImageTab.reset_images(tab)
            tab._reset_images_confirmed.assert_called_once_with()

    def test_confirmed_reset_uses_the_clear_image_sound_once(self) -> None:
        card = SimpleNamespace(
            release_asset_handle=mock.Mock(),
            clear_pixmap=mock.Mock(),
        )
        tab = SimpleNamespace(
            labels={1: ("Cover Page", "cover.png")},
            cards={1: card},
            image_paths={1: "cover.png"},
            project_state=SimpleNamespace(is_project_ready=False),
            _user_pages_dir=mock.Mock(return_value="pages"),
            clear_preview=SimpleNamespace(emit=mock.Mock()),
            _commit_image_change=mock.Mock(),
            _show_temporary_status=mock.Mock(),
        )

        with (
            mock.patch("Image_tab.clear_slot_asset"),
            mock.patch("Image_tab.play_ui_sound") as play_sound,
        ):
            ImageTab._reset_images_confirmed(tab)

        play_sound.assert_called_once_with(UiSound.REMOVED)

    def test_image_utility_buttons_preserve_artwork_and_do_not_overlap_cards(self) -> None:
        expected_size = BUTTON_TIER_STYLES[ButtonTier.LARGE].size

        with tempfile.TemporaryDirectory() as directory:
            image_tab = ImageTab(directory)
            image_tab.resize(1180, 340)
            image_tab.show()
            self.app.processEvents()
            image_tab.reset_btn.apply_theme_assets(None)
            image_tab.open_btn.apply_theme_assets(None)
            self.app.processEvents()
            try:
                self.assertEqual(
                    image_tab.reset_btn.size(),
                    expected_size,
                )
                self.assertEqual(
                    image_tab.open_btn.size(),
                    expected_size,
                )
                self.assertEqual(image_tab.reset_btn.property("buttonTier"), "large")
                self.assertEqual(image_tab.open_btn.property("buttonTier"), "large")
                self.assertFalse(image_tab.reset_btn._artwork_stretch)
                self.assertFalse(image_tab.open_btn._artwork_stretch)
                for button in (
                    image_tab.reset_btn,
                    image_tab.open_btn,
                ):
                    self.assertFalse(
                        button.geometry().intersects(
                            image_tab.cards[1].geometry()
                        )
                    )
            finally:
                image_tab.close()


class ImageTabPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    @staticmethod
    def _tab_stub(fingerprint: str = "same") -> SimpleNamespace:
        return SimpleNamespace(
            _disk_fingerprint=fingerprint,
            _project_dir=mock.Mock(return_value="project"),
            refresh_cards=mock.Mock(return_value=False),
            _emit_cover_change_if_needed=mock.Mock(return_value=False),
            images_changed=mock.Mock(),
        )

    def test_unchanged_sync_skips_card_rebuild_and_second_fingerprint(self) -> None:
        tab = self._tab_stub()

        with mock.patch(
            "Image_tab.image_fingerprint",
            return_value="same",
        ) as fingerprint:
            changed = ImageTab.sync_from_disk(tab)

        self.assertFalse(changed)
        fingerprint.assert_called_once_with("project")
        tab.refresh_cards.assert_not_called()
        tab.images_changed.emit.assert_not_called()

    def test_reconciliation_is_the_only_sync_that_refingerprints(self) -> None:
        tab = self._tab_stub("before")
        tab.refresh_cards.return_value = True

        with mock.patch(
            "Image_tab.image_fingerprint",
            side_effect=("external", "reconciled"),
        ) as fingerprint:
            changed = ImageTab.sync_from_disk(tab)

        self.assertTrue(changed)
        self.assertEqual(fingerprint.call_count, 2)
        self.assertEqual(tab._disk_fingerprint, "reconciled")
        tab.refresh_cards.assert_called_once_with()
        tab.images_changed.emit.assert_called_once_with("disk")

    def test_unchanged_sync_to_disk_skips_card_rebuild(self) -> None:
        tab = self._tab_stub()

        with mock.patch(
            "Image_tab.image_fingerprint",
            return_value="same",
        ) as fingerprint:
            changed = ImageTab.sync_to_disk(tab)

        self.assertFalse(changed)
        fingerprint.assert_called_once_with("project")
        tab.refresh_cards.assert_not_called()
        tab.images_changed.emit.assert_not_called()

    def test_image_preprocessing_runs_off_ui_thread_and_rejects_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            SettingsStore(root).update_fields(
                {"recipient_title": "Morning Joy"}
            )
            pages = root / "gallery/user/pages"
            pages.mkdir(parents=True)
            for name in ("letter.png", "wall.png", "back.png"):
                Image.new("RGBA", (16, 16), "red").save(
                    pages / name,
                    format="PNG",
                )
            source = root / "replacement.png"
            Image.new("RGBA", (512, 512), "green").save(source, format="PNG")
            tab = ImageTab(root, project_state=state)
            self.assertFalse(tab.project_save_service.can_save())
            started = threading.Event()
            release = threading.Event()
            worker_threads: list[QtCore.QThread] = []

            def delayed_prepare(*args, **kwargs):
                worker_threads.append(QtCore.QThread.currentThread())
                started.set()
                if not release.wait(5):
                    raise RuntimeError("Test image import did not resume.")
                return prepare_image_asset_import(*args, **kwargs)

            try:
                with mock.patch(
                    "Image_tab.prepare_image_asset_import",
                    side_effect=delayed_prepare,
                ):
                    tab.set_image_path(1, str(source))
                    self.assertTrue(started.wait(2))
                    first_thread = tab._image_import_thread
                    first_generation = tab._image_import_generation

                    tab.set_image_path(1, str(source))

                    self.assertIs(tab._image_import_thread, first_thread)
                    self.assertEqual(tab._image_import_generation, first_generation)
                    release.set()
                    deadline = time.monotonic() + 5
                    while tab._image_import_thread is not None:
                        self.app.processEvents()
                        if time.monotonic() >= deadline:
                            self.fail("Image import thread did not finish.")
                        time.sleep(0.01)

                self.assertTrue(worker_threads)
                self.assertIsNot(worker_threads[0], self.app.thread())
                with Image.open(root / "gallery/user/pages/cover.png") as installed:
                    self.assertEqual(installed.getpixel((0, 0))[:3], (0, 128, 0))
                context = tab.project_save_service.current_context()
                self.assertTrue(
                    (context.autosave_directory / "pages/cover.png").is_file()
                )
            finally:
                release.set()
                tab.shutdown()
                tab.close()
                state.shutdown()

    def test_project_restore_invalidates_late_image_import_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            tab = ImageTab(root, project_state=state)
            generation = tab._image_import_generation
            worker = mock.Mock()
            worker_thread = mock.Mock()
            worker_thread.isRunning.return_value = True
            worker_thread.wait.return_value = False
            tab._image_import_thread = worker_thread
            tab._image_import_worker = worker
            tab._image_import_result_holder = {}
            tab._image_import_index = 1

            try:
                with self.assertRaises(RuntimeError):
                    tab.prepare_for_project_restore(timeout_ms=10)
                worker.cancel.assert_called_once_with()
                worker_thread.requestInterruption.assert_called_once_with()
                worker_thread.wait.assert_called_once_with(10)
                worker_thread.setParent.assert_called_once_with(None)
                self.assertEqual(tab._image_import_generation, generation + 1)
                self.assertIsNone(tab._image_import_thread)

                prepared = mock.Mock()
                result = _PreparedImageImportResult(
                    generation=generation,
                    index=1,
                    project_identity=("recipient", "project"),
                    baseline_fingerprint="baseline",
                    project_pages_directory=None,
                    prepared=prepared,
                    preview_image=QtGui.QImage(),
                )
                holder = {"result": result}
                tab._image_import_result_holder = holder
                tab._image_import_prepared(result)
                prepared.abort.assert_called_once_with()
                self.assertNotIn("result", holder)

                self.assertFalse(
                    (root / "gallery/user/pages/cover.png").exists()
                )
            finally:
                tab.shutdown()
                tab.close()
                state.shutdown()

    def test_movie_pauses_and_resumes_without_reconstruction(self) -> None:
        movie = _FakeMovie(QtGui.QMovie.Running)
        card = SimpleNamespace(
            _movie=movie,
            _playback_active=True,
            _resume_movie_on_activation=False,
        )

        ImageAssetCard.set_playback_active(card, False)

        self.assertEqual(movie.state(), QtGui.QMovie.Paused)
        self.assertTrue(card._resume_movie_on_activation)

        ImageAssetCard.set_playback_active(card, True)

        self.assertEqual(movie.state(), QtGui.QMovie.Running)
        self.assertFalse(card._resume_movie_on_activation)
        self.assertEqual(movie.start_calls, 0)

    def test_real_gif_card_applies_speed_and_survives_tab_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gif"
            ImageAnimationTests._write_gif(source)
            card = ImageAssetCard(1, "Cover Page", root)
            self.addCleanup(card.deleteLater)
            card.set_playback_active(False)
            card.set_asset_path(
                str(source),
                animated_gif=True,
                settings={"animation_enabled": True, "speed_percent": 175},
                animate_gif=False,
            )

            movie = card._movie
            self.assertIsNotNone(movie)
            self.assertEqual(movie.speed(), 175)
            card.set_playback_active(True)
            self.app.processEvents()
            self.assertIs(card._movie, movie)
            self.assertEqual(movie.state(), QtGui.QMovie.Running)

            card.set_playback_active(False)
            self.assertIs(card._movie, movie)
            self.assertEqual(movie.state(), QtGui.QMovie.Paused)
            card.set_playback_active(True)
            self.assertIs(card._movie, movie)
            self.assertEqual(movie.state(), QtGui.QMovie.Running)
            card.release_asset_handle()
            self.app.processEvents()

    def test_stopped_gif_card_keeps_preview_frame_without_movie(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.gif"
            ImageAnimationTests._write_gif(source)
            card = ImageAssetCard(1, "Cover Page", root)
            self.addCleanup(card.deleteLater)
            card.set_asset_path(
                str(source),
                animated_gif=True,
                settings={"animation_enabled": False, "speed_percent": 200},
            )

            self.assertIsNone(card._movie)
            self.assertIsNotNone(card.thumbnail.pixmap())
            self.assertFalse(card.thumbnail.pixmap().isNull())
            self.assertTrue(card.settings_btn.isEnabled())

    def test_completed_movie_is_not_restarted_on_tab_activation(self) -> None:
        movie = _FakeMovie(QtGui.QMovie.NotRunning)
        card = SimpleNamespace(
            _movie=movie,
            _playback_active=True,
            _resume_movie_on_activation=False,
        )

        ImageAssetCard.set_playback_active(card, False)
        ImageAssetCard.set_playback_active(card, True)

        self.assertEqual(movie.state(), QtGui.QMovie.NotRunning)
        self.assertEqual(movie.start_calls, 0)

    def test_tab_lifecycle_controls_all_card_movies(self) -> None:
        cards = {
            1: mock.Mock(),
            2: mock.Mock(),
        }
        tab = SimpleNamespace(
            _tab_active=True,
            cards=cards,
            sync_from_disk=mock.Mock(),
            sync_to_disk=mock.Mock(),
        )

        ImageTab.deactivate_for_tab_change(tab)

        self.assertFalse(tab._tab_active)
        tab.sync_to_disk.assert_called_once_with()
        for card in cards.values():
            card.set_playback_active.assert_called_once_with(False)
            card.release_asset_handle.assert_not_called()

        ImageTab.activate_for_tab_change(tab)

        self.assertTrue(tab._tab_active)
        tab.sync_from_disk.assert_called_once_with()
        for card in cards.values():
            card.set_playback_active.assert_called_with(True)


if __name__ == "__main__":
    unittest.main()
