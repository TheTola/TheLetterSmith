from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6 import QtCore, QtWidgets

from config import CONTROL_FILES, validate_required_images
from generate import APP_BANNER_PATH, generate_play_bundle, validate_play_bundle
from image_animation import (
    build_runtime_image_assets, install_image_asset, update_slot_gif_settings,
    validate_runtime_image_manifest,
)
from Image_tab import ImageTab
from page_layout import (
    DEFAULT_PAGES, PAGE_INDICES, LetterPage, load_pages, normalize_pages,
    page_labels, pages_payload, resized_pages, save_pages,
)
from PromptWriterPanel import PromptWriterPanel
from readiness import evaluate_readiness
from saved_letters import SavedLetterCatalog, SavedLetterRestorer, update_saved_metadata
from settings_store import SettingsStore
from Template import template_for_pages


class _Language:
    def correct_text(self, text, **kwargs):
        return text


class PageLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _images(self, directory, pages):
        directory.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(pages):
            Image.new("RGB", (24, 36), (index * 20, 90, 140)).save(directory / page.filename)

    def _panel(self, root):
        modules = root / "Prompter/modules"
        modules.mkdir(parents=True, exist_ok=True)
        (modules / "topic.txt").write_text("Garden\n", encoding="utf-8")
        for name, value in {"role": "Artist", "type": "Illustration", "color": "Blue", "order": "Create", "effort": "High quality", "format": "Portrait image"}.items():
            (modules / f"{name}.txt").write_text(value + "\n", encoding="utf-8")
        panel = PromptWriterPanel(project_root=str(root), language_service=_Language())
        panel.cmb_subject.setCurrentText("Garden")
        return panel

    def test_legacy_layout_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_pages(directory), DEFAULT_PAGES)
            for count in range(2, 11):
                pages = resized_pages(DEFAULT_PAGES, count)
                self.assertEqual(len(pages), count)
                self.assertEqual(pages[0], LetterPage("cover", "cover"))
                self.assertEqual(pages[-1], LetterPage("back", "back"))
                save_pages(directory, pages)
                self.assertEqual(load_pages(directory), pages)

    def test_invalid_layouts_are_rejected(self):
        invalid = [[], pages_payload(DEFAULT_PAGES) * 3,
                   [{"key": "../outside", "kind": "cover"}, {"key": "back", "kind": "back"}],
                   [{"key": "cover", "kind": "cover"}, {"key": "cover", "kind": "wall"}, {"key": "back", "kind": "back"}]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_pages(value)

    def test_multiple_walls_have_clear_labels_and_one_message_overlay(self):
        pages = (DEFAULT_PAGES[0], LetterPage("letter", "wall"),
                 LetterPage("wall", "wall"), LetterPage("page_1", "letter"), DEFAULT_PAGES[-1])
        self.assertEqual(list(page_labels(pages).values()), ["Cover", "Letter 1", "Letter 2", "Illustration", "End Page"])
        html = template_for_pages(pages)
        self.assertEqual(html.count('<section class="slide'), 5)
        self.assertEqual(html.count('data-page-type="wall"'), 2)
        self.assertEqual(html.count('id="textWall"'), 1)
        self.assertLess(html.index('gallery/pages/page_1.png'), html.index('gallery/pages/back.png'))

    def test_two_pages_need_only_cover_and_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = resized_pages(DEFAULT_PAGES, 2)
            save_pages(root / "gallery/user/pages", pages)
            self._images(root / "gallery/user/pages", pages)
            self.assertEqual(validate_required_images(root), [])
            result = evaluate_readiness(root)
            image_items = [item for item in result.items if item.correction_tab == "images"]
            self.assertEqual([item.key for item in image_items], ["cover", "back"])
            self.assertTrue(all(item.ready for item in image_items))

    def test_reordered_extra_gif_uses_its_actual_slide_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = list(resized_pages(DEFAULT_PAGES, 6))
            pages[1], pages[3] = pages[3], pages[1]
            pages = tuple(pages)
            save_pages(root / "source", pages)
            self._images(root / "source", pages)
            source = root / "animation.gif"
            frames = [Image.new("RGB", (12, 18), color) for color in ("red", "blue", "green")]
            frames[0].save(source, save_all=True, append_images=frames[1:], duration=70, loop=0)
            install_image_asset(root / "source", "page_1", source)
            update_slot_gif_settings(root / "source", "page_1", {"start_delay_ms": 100})
            runtime = build_runtime_image_assets(root / "source", root / "runtime")
            self.assertIn("1", runtime.animations)
            self.assertEqual(load_pages(root / "runtime"), pages)
            self.assertEqual(set(validate_runtime_image_manifest(root / "runtime")["slots"]), {page.key for page in pages})

    def test_editors_share_count_type_order_and_keep_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tab = ImageTab(root)
            panel = self._panel(root)
            try:
                tab.page_controls.count.setValue(6)
                self.assertEqual(len(panel._page_specs), 6)
                panel._all_page_specs["page_1"].detail_widget.setPlainText("Blue garden")
                panel.page_controller.select("page_1")
                panel.page_controls.kind.setCurrentIndex(panel.page_controls.kind.findData("wall"))
                panel.page_controls.move_left.click()
                self.assertEqual(tab.page_controller.pages[2], LetterPage("page_1", "wall"))
                self.assertEqual(tab.labels[5][0], "Letter 1 Image")
                self.assertEqual(panel._page_specs[2].detail_widget.toPlainText(), "Blue garden")
                panel._on_generate()
                self.assertEqual(len(panel._generated_prompts), 6)
                self.assertIn("The Wall Page", panel._generated_prompts["page_1"])
                panel._copy_prompt("page_1")
                self.assertEqual(self.app.clipboard().text(), panel._generated_prompts["page_1"])
                panel.page_controls.count.setValue(2)
                self.assertFalse(panel._generated_output_valid)
                self.assertEqual(list(tab.cards), [1, 4])
                panel.page_controls.count.setValue(10)
                self.assertEqual(len(panel._page_specs), 10)
                self.assertEqual(panel._all_page_specs["page_1"].detail_widget.toPlainText(), "Blue garden")
                panel._on_generate()
                self.assertTrue(panel._generated_output_valid)
                self.assertEqual(len(panel._generated_prompts), 10)
                panel.persist_project_state()
                panel.reload_project_state()
                self.assertTrue(panel._generated_output_valid)
                self.assertEqual(len(panel._generated_prompts), 10)
            finally:
                panel.shutdown()
                tab.shutdown()
                panel.deleteLater()
                tab.deleteLater()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_image_approval_uses_stable_slot_after_reordering(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = self._panel(Path(directory))
            try:
                pages = list(resized_pages(DEFAULT_PAGES, 5))
                pages[1], pages[3] = pages[3], pages[1]
                panel.page_controller.update_pages(tuple(pages))
                panel._image_tab = mock.Mock()
                panel._image_tab.set_image_path.return_value = True
                panel._on_browser_image_ready("page_1", "selected.png")
                panel._image_tab.set_image_path.assert_called_once_with(PAGE_INDICES["page_1"], "selected.png")
            finally:
                panel.shutdown()
                panel.deleteLater()
                self.app.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_full_export_and_saved_restore_with_two_and_ten_pages(self):
        for count in (2, 10):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                pages = tuple(replace(page, kind="wall") if page.key in {"letter", "page_1"} else page
                              for page in resized_pages(DEFAULT_PAGES, count))
                save_pages(root / "gallery/user/pages", pages)
                self._images(root / "gallery/user/pages", pages)
                controls = root / "gallery/user/card/controls"
                controls.mkdir(parents=True)
                for name in CONTROL_FILES:
                    Image.new("RGB", (12, 12), "white").save(controls / name)
                banner = root / APP_BANNER_PATH
                banner.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (12, 12), "gold").save(banner)
                SettingsStore(root).update_fields({"recipient_name": "Amanda Miller", "recipient_title": "Garden Pages"})
                play = generate_play_bundle(str(root), message_html="<p>My shared message</p>", seed_sfx=False)
                validate_play_bundle(play)
                self.assertEqual(load_pages(play / "gallery/pages"), pages)
                html = (play / "index.html").read_text(encoding="utf-8")
                self.assertEqual(html.count('<section class="slide'), count)
                self.assertEqual(html.count('data-page-type="wall"'), sum(page.kind == "wall" for page in pages))
                update_saved_metadata(play, root, evaluate_readiness(root))
                entries = SavedLetterCatalog(root).list_entries()
                self.assertEqual(len(entries), 1)
                save_pages(root / "gallery/user/pages", DEFAULT_PAGES)
                SavedLetterRestorer(root).restore(entries[0])
                self.assertEqual(load_pages(root / "gallery/user/pages"), pages)
