from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock
from uuid import uuid4

from PIL import Image
from PySide6 import QtCore, QtTest, QtWidgets

from Image_tab import ImageTab
from project_consumers import project_legacy_views
from project_model import MAX_PAGES, MIN_PAGES, PAGE_TYPES, ProjectModel, create_project, empty_message
from project_repository import active_project_repository
from project_session import get_project_session, release_project_session
from test_project_model import five_page_project


class ImagesWorkspaceTests(unittest.TestCase):
    """Acceptance checks against the real session, media controller and widget."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.session = get_project_session(self.root)
        project = five_page_project()
        (self.root / "assets").mkdir()
        for index, page in enumerate(project.pages):
            Image.new("RGB", (120, 180), (index * 45, 30, 200 - index * 35)).save(
                self.root / page["artwork"]["asset"])
        self.session.replace(project, asset_root=self.root)
        self.ids = [page["id"] for page in self.session.project.pages]
        self.first, self.second = self.ids[1], self.ids[3]
        self.session.project.rename_page(self.first, "Repeated Letter")
        self.session.project.rename_page(self.second, "Repeated Letter")
        project_legacy_views(self.session)
        self.session.save(sync_workspace=False)
        self.tab = ImageTab(self.root)
        self.tab.resize(1050, 700)
        self.tab.show()
        self.pump()

    def tearDown(self):
        self.tab.shutdown()
        self.tab.close()
        self.tab.deleteLater()
        self.pump()
        release_project_session(self.root)
        self.directory.cleanup()

    def pump(self):
        self.app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.pump()
            QtTest.QTest.qWait(10)
        self.pump()
        self.assertTrue(predicate(), "Timed out waiting for the production import worker")

    def image(self, name="replacement.png", color="gold"):
        path = self.root / name
        Image.new("RGB", (160, 240), color).save(path)
        return path

    def gif(self):
        path = self.root / "animated.gif"
        frames = [Image.new("RGB", (40, 60), color) for color in ("red", "blue")]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=[90, 170], loop=2)
        return path

    def page(self, page_id):
        return self.session.project.get_page(page_id)

    def assert_same_content(self, actual, expected):
        for field in ("id", "name", "type", "artwork", "message", "prompt", "design_overrides"):
            self.assertEqual(actual[field], expected[field], field)

    def select(self, page_id):
        self.session.select_page(page_id)
        self.pump()

    def list_ids(self):
        return [self.tab.page_list.item(index).data(QtCore.Qt.UserRole)
                for index in range(self.tab.page_list.count())]

    def row_text(self, page_id):
        item = self.tab.page_list.item(self.list_ids().index(page_id))
        return item.text() + '\n' + (item.data(QtCore.Qt.UserRole+2) or '')

    def import_and_wait(self, page_id, path):
        self.assertTrue(self.tab.import_image(page_id, str(path)))
        self.wait_for(lambda: self.tab._image_import_thread is None)

    def test_five_pages_and_repeated_names_keep_exact_selection_and_navigation_ids(self):
        self.assertIs(self.tab.session, self.session)
        self.assertEqual(self.list_ids(), self.ids)
        prompt_ids, message_ids = [], []
        self.tab.prompt_requested.connect(prompt_ids.append)
        self.tab.message_requested.connect(message_ids.append)
        for page_id in (self.first, self.second):
            self.select(page_id)
            self.assertTrue(self.tab.message_button.isEnabled())
            self.tab.request_prompt()
            self.tab.request_message()
        self.assertEqual(prompt_ids, [self.first, self.second])
        self.assertEqual(message_ids, [self.first, self.second])
        for page_id in (self.ids[0], self.ids[2], self.ids[4]):
            self.select(page_id)
            self.assertFalse(self.tab.message_button.isEnabled())
            self.tab.request_message()
        self.assertEqual(message_ids, [self.first, self.second])

    def test_each_official_type_is_inserted_after_selection_and_selected(self):
        for kind in PAGE_TYPES:
            self.select(self.first)
            before = [page["id"] for page in self.session.project.pages]
            page_id = self.tab.add_page(kind)
            self.pump()
            self.assertEqual(self.page(page_id)["type"], kind)
            self.assertEqual(self.session.selected_page_id, page_id)
            self.assertEqual(self.list_ids().index(page_id), before.index(self.first) + 1)
            self.assertEqual(self.tab.message_button.isEnabled(), kind == "letter")
            self.assertEqual(self.page(page_id)["message"], empty_message() if kind == "letter" else None)
        self.assertEqual(len(self.list_ids()), 9)

    def test_artwork_launcher_tracks_selection_reorder_rename_and_empty_project(self):
        from Image_tab import StaticFab
        from project_paths import application_paths
        from types import SimpleNamespace

        button = self.tab.pwrite_fab
        button.apply_theme_assets(SimpleNamespace(
            resolve_asset=lambda name: application_paths().app_resource_path("themes/cyber_forge/" + name)
        ))
        self.assertIsInstance(button, StaticFab)
        self.assertFalse(button.icon().isNull())
        requested = []
        self.tab.prompt_requested.connect(requested.append)
        self.select(self.second)
        self.tab.rename_page(self.second, "Moonlit garden")
        self.tab.reorder_pages(list(reversed(self.ids)))
        self.pump()
        self.assertIn("Moonlit garden", button.accessibleDescription())
        self.assertIn("Page 2", self.tab.prompt_target.text())
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.select(self.ids[2])
        button.setFocus()
        QtTest.QTest.keyClick(button, QtCore.Qt.Key_Space)
        self.assertEqual(requested, [self.second, self.ids[2]])
        self.session.select_page(None)
        self.pump()
        self.assertFalse(button.isEnabled())
        self.assertEqual(self.tab.prompt_target.text(), "Select a page to begin")
        QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
        self.assertEqual(requested, [self.second, self.ids[2]])

    def test_artwork_launcher_does_not_open_when_pending_edits_cannot_save(self):
        requested = []
        self.tab.prompt_requested.connect(requested.append)
        with mock.patch.object(self.tab, "flush_pending_edits", return_value=False):
            QtTest.QTest.mouseClick(self.tab.pwrite_fab, QtCore.Qt.LeftButton)
        self.assertEqual(requested, [])

    def test_creation_area_reflows_without_covering_the_preview(self):
        for width, height in ((1200, 760), (960, 620), (800, 760), (560, 1000), (1200, 760)):
            with self.subTest(width=width, height=height):
                self.tab.resize(width, height)
                self.pump()
                rectangles = [QtCore.QRect(control.mapTo(self.tab, QtCore.QPoint()), control.size())
                              for control in (self.tab.page_list, self.tab.pwrite_fab, self.tab.import_button,
                                              self.tab.stock_button)]
                for rect in rectangles:
                    self.assertTrue(self.tab.rect().contains(rect), (self.tab.size(), rect))
                for rect in rectangles[1:]:
                    self.assertFalse(rect.intersects(rectangles[0]))
                self.assertEqual(self.tab.page_list.viewMode(), QtWidgets.QListView.IconMode)

    def test_add_without_selection_appends(self):
        self.session.select_page(None)
        page_id = self.tab.add_page("illustration")
        self.assertEqual(self.session.project.pages[-1]["id"], page_id)
        self.assertEqual(self.session.selected_page_id, page_id)

    def test_grid_drop_changes_canonical_order_without_changing_page_content(self):
        navigator = self.tab.page_list
        before = self.page(self.ids[0])
        mime = QtCore.QMimeData()
        mime.setData(navigator._PAGE_MIME, self.ids[0].encode('utf-8'))
        event = mock.Mock()
        event.source.return_value = navigator
        event.mimeData.return_value = mime
        event.position.return_value = QtCore.QPointF(navigator.visualItemRect(navigator.item(2)).center())
        navigator.dropEvent(event)
        self.assertEqual(self.list_ids()[:3], [self.ids[1], self.ids[2], self.ids[0]])
        after = self.page(self.ids[0])
        for field in ('id', 'type', 'artwork', 'message'):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after['prompt']['text'], before['prompt']['text'])
        event.accept.assert_called_once()

    def test_rename_reorder_and_explicit_move_preserve_page_owned_content(self):
        self.select(self.second)
        original = self.page(self.second)
        source = self.tab.artwork.resolve_asset(self.second).read_bytes()
        self.tab.rename_page(self.second, "Second Letter — " + "long readable name " * 12)
        self.tab.reorder_pages(list(reversed(self.ids)))
        self.tab.move_page(self.second, 1)
        self.pump()
        current = self.page(self.second)
        self.assertEqual(self.session.selected_page_id, self.second)
        self.assertEqual(self.list_ids(), [page["id"] for page in self.session.project.pages])
        self.assertEqual(current["message"], original["message"])
        self.assertEqual(current["artwork"], original["artwork"])
        self.assertEqual(current["prompt"]["text"], original["prompt"]["text"])
        self.assertEqual(self.tab.artwork.resolve_asset(self.second).read_bytes(), source)
        self.assertIn("Second Letter", self.tab.header.text())

    def test_duplicate_crop_does_not_modify_original_or_shared_source(self):
        source = self.tab.artwork.resolve_asset(self.first)
        source_bytes = source.read_bytes()
        original = self.page(self.first)
        duplicate = self.tab.duplicate_page(self.first)
        self.assertNotEqual(duplicate, self.first)
        self.assertEqual(self.session.selected_page_id, duplicate)
        self.assertTrue(self.tab.artwork.crop(duplicate, (10, 10, 100, 160)))
        self.assertEqual(self.page(self.first)["artwork"], original["artwork"])
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertNotEqual(self.page(duplicate)["artwork"]["asset"], original["artwork"]["asset"])
        document = self.page(duplicate)["message"]
        document["html"] = "<p>Only the duplicated letter.</p>"
        self.session.project.update_message(duplicate, document)
        self.session.project.update_prompt(duplicate, {"text": "Only the duplicated prompt."})
        self.assertEqual(self.page(self.first)["message"], original["message"])
        self.assertEqual(self.page(self.first)["prompt"]["text"], original["prompt"]["text"])

    def test_type_round_trip_hides_and_restores_message_and_preserves_prompt_and_artwork(self):
        original = self.page(self.first)
        self.select(self.first)
        self.tab.change_page_type(self.first, "illustration")
        self.assertIsNone(self.page(self.first)["message"])
        self.assertFalse(self.tab.message_button.isEnabled())
        self.assertEqual(self.page(self.first)["artwork"], original["artwork"])
        self.assertEqual(self.page(self.first)["prompt"]["text"], original["prompt"]["text"])
        self.assertTrue(self.page(self.first)["prompt"]["stale"])
        self.tab.change_page_type(self.first, "letter")
        self.assertEqual(self.page(self.first)["message"], original["message"])
        self.assertTrue(self.tab.message_button.isEnabled())

    def test_clear_image_preserves_page_message_prompt_and_other_letter(self):
        original, other = self.page(self.first), self.page(self.second)
        original_file = self.tab.artwork.resolve_asset(self.first)
        self.tab.clear_page_image(self.first)
        current = self.page(self.first)
        self.assertIsNone(current["artwork"]["asset"])
        self.assertEqual(current["message"], original["message"])
        self.assertEqual(current["prompt"]["text"], original["prompt"]["text"])
        self.assert_same_content(self.page(self.second), other)
        self.assertTrue(original_file.exists())
        self.assertEqual(len(self.list_ids()), 5)

    def test_invalid_and_aborted_staging_leave_old_artwork_intact(self):
        original = self.page(self.first)
        invalid = self.root / "invalid.png"
        invalid.write_bytes(b"This is not an image.")
        with self.assertRaises((ValueError, OSError)):
            self.tab.artwork.stage_import(invalid)
        self.assertEqual(self.page(self.first), original)
        prepared = self.tab.artwork.stage_import(self.image())
        target = self.tab.artwork.capture_target(self.first)
        prepared.abort()
        self.assertFalse(self.tab.artwork.apply_import(target, prepared))
        self.assertEqual(self.page(self.first), original)

    def test_import_after_selection_change_stays_with_original_letter(self):
        other = self.page(self.second)
        completed = []
        self.tab.artwork_import_completed.connect(lambda *args: completed.append(args))
        self._delayed_import(self.first, lambda: self.select(self.second))
        self.assert_same_content(self.page(self.second), other)
        self.assertEqual(self.session.selected_page_id, self.second)
        self.assertEqual(completed[-1][0], self.first)
        self.assertEqual(completed[-1][2], "")
        with Image.open(self.tab.artwork.resolve_asset(self.first)) as image:
            self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 215, 0))

    def _delayed_import(self, page_id, while_staging):
        entered, release = threading.Event(), threading.Event()
        real_stage = self.tab.artwork.stage_import
        threads = []

        def delayed(*args, **kwargs):
            threads.append(QtCore.QThread.currentThread())
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Synthetic staging barrier timed out")
            return real_stage(*args, **kwargs)

        with mock.patch.object(self.tab.artwork, "stage_import", side_effect=delayed):
            try:
                self.assertTrue(self.tab.import_image(page_id, str(self.image())))
                self.assertTrue(entered.wait(3), "Import staging never started")
                self.assertIsNot(threads[0], self.app.thread())
                while_staging()
            finally:
                release.set()
            self.wait_for(lambda: self.tab._image_import_thread is None)

    def test_deleted_target_during_import_cannot_modify_remaining_pages(self):
        def remove():
            self.tab.delete_page(self.first, confirm=False)
            self.after_delete = self.session.project.to_dict()
        self._delayed_import(self.first, remove)
        self.assertEqual(self.session.project.to_dict(), self.after_delete)
        self.assertNotIn(self.first, self.list_ids())

    def test_replaced_project_during_import_cannot_receive_old_result(self):
        replacement = create_project(("cover", "letter", "end_page"))
        def replace():
            self.session.replace(replacement, asset_root=self.root)
            self.after_replace = self.session.project.to_dict()
        self._delayed_import(self.first, replace)
        self.assertEqual(self.session.project.to_dict(), self.after_replace)
        self.assertEqual(self.list_ids(), [page["id"] for page in replacement.pages])

    def test_changed_target_artwork_during_import_keeps_the_newer_edit(self):
        def edit():
            self.tab.clear_page_image(self.first)
            self.after_edit = self.session.project.to_dict()
        self._delayed_import(self.first, edit)
        self.assertEqual(self.session.project.to_dict(), self.after_edit)

    def test_gif_source_survives_import_and_controls_follow_actual_media(self):
        source = self.gif()
        source_bytes = source.read_bytes()
        self.select(self.first)
        self.import_and_wait(self.first, source)
        self.assertEqual(self.tab.artwork.resolve_asset(self.first).read_bytes(), source_bytes)
        self.assertTrue(self.tab.animation_button.isVisible())
        self.assertTrue(self.tab.animation_button.isEnabled())
        with Image.open(self.tab.artwork.resolve_asset(self.first)) as image:
            self.assertEqual(image.n_frames, 2)
            self.assertEqual(image.info["loop"], 2)
        self.import_and_wait(self.first, self.image())
        self.assertFalse(self.tab.animation_button.isVisible() and self.tab.animation_button.isEnabled())
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_replacement_resets_only_target_placement_and_keeps_exact_prompt_text(self):
        self.session.project.update_artwork(self.first, {"placement": {"fit": "contain", "x": 15}})
        self.session.project.update_artwork(self.second, {"placement": {"fit": "cover", "x": -25}})
        original, other = self.page(self.first), self.page(self.second)
        self.import_and_wait(self.first, self.image())
        self.assertEqual(self.page(self.first)["artwork"]["placement"], {})
        self.assertEqual(self.page(self.first)["prompt"]["text"], original["prompt"]["text"])
        self.assertEqual(self.page(self.first)["message"], original["message"])
        self.assert_same_content(self.page(self.second), other)

    def test_crop_rejects_changed_dialog_target_and_retains_animated_source(self):
        captured = self.tab.artwork.capture_target(self.first)
        self.tab.clear_page_image(self.first)
        after_clear = self.session.project.to_dict()
        self.assertFalse(self.tab.artwork.crop(self.first, (0, 0, 30, 40), target=captured))
        self.assertEqual(self.session.project.to_dict(), after_clear)
        self.import_and_wait(self.first, self.gif())
        source = self.tab.artwork.resolve_asset(self.first)
        original = source.read_bytes()
        before = self.page(self.first)
        with self.assertRaises(ValueError):
            self.tab.artwork.crop(self.first, (0, 0, 30, 40))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.page(self.first), before)

    def test_page_limits_and_out_of_range_import_keep_all_pages_visible(self):
        self.session.replace(create_project(("illustration",) * MAX_PAGES), asset_root=self.root)
        self.pump()
        self.assertTrue(self.tab.add_button.isEnabled())
        self.assertFalse(self.tab.limit_label.isVisible())
        with self.assertRaisesRegex(ValueError, 'maximum of 10'):
            self.tab.add_page('illustration')
        self.session.replace(create_project(("illustration",) * MIN_PAGES), asset_root=self.root)
        self.pump()
        self.assertFalse(self.tab.delete_button.isEnabled())
        imported = create_project(("illustration",) * MAX_PAGES).to_dict()
        for _ in range(2):
            page = deepcopy(imported["pages"][0])
            page["id"] = str(uuid4())
            imported["pages"].append(page)
        self.session.replace(ProjectModel.from_dict(imported), asset_root=self.root)
        self.pump()
        self.assertEqual(self.tab.page_list.count(), MAX_PAGES + 2)
        self.assertTrue(self.tab.add_button.isEnabled())
        self.select(imported["pages"][-1]["id"])
        self.assertEqual(self.tab.page_list.currentRow(), MAX_PAGES + 1)

    def test_readiness_uses_image_and_message_without_requiring_prompt(self):
        page_id = self.ids[2]
        self.assertIsNone(self.page(page_id)["prompt"]["text"])
        self.assertIn("Ready", self.row_text(page_id))
        self.tab.clear_page_image(page_id)
        self.assertIn("Image missing", self.row_text(page_id))
        self.session.project.update_message(self.first, empty_message())
        self.assertIn("Ready", self.row_text(self.first))
        self.assertIn("Ready", self.row_text(self.second))

    def test_delete_undo_restores_identity_content_order_and_keeps_source_files(self):
        original = self.page(self.first)
        source = self.tab.artwork.resolve_asset(self.first)
        self.select(self.first)
        self.tab.delete_page(self.first, confirm=False)
        self.assertNotIn(self.first, self.list_ids())
        self.assertIn(self.session.selected_page_id, self.list_ids())
        self.assertTrue(source.exists())
        self.tab.undo_delete()
        self.assertEqual(self.list_ids(), self.ids)
        self.assertEqual(self.page(self.first)["message"], original["message"])
        self.assertEqual(self.page(self.first)["artwork"], original["artwork"])
        self.assertEqual(self.page(self.first)["prompt"]["text"], original["prompt"]["text"])

    def test_save_reload_and_new_project_do_not_restore_previous_content(self):
        self.import_and_wait(self.first, self.gif())
        self.tab.reorder_pages(list(reversed(self.ids)))
        self.session.save(sync_workspace=False)
        stored = active_project_repository(self.root).load()
        expected = stored.project.to_dict()
        expected_gif = (stored.asset_root / stored.project.get_page(self.first)["artwork"]["asset"]).read_bytes()
        self.session.replace(create_project(("cover", "letter", "end_page")), asset_root=self.root)
        self.assertTrue(set(self.ids).isdisjoint(self.list_ids()))
        self.assertTrue(all(page["artwork"]["asset"] is None for page in self.session.project.pages))
        self.session.replace(stored.project, storage_revision=stored.revision, asset_root=stored.asset_root)
        self.tab.refresh_from_disk()
        self.assertEqual(self.session.project.to_dict(), expected)
        self.assertEqual(self.list_ids(), list(reversed(self.ids)))
        self.assertEqual(self.tab.artwork.resolve_asset(self.first).read_bytes(), expected_gif)
        self.assertEqual(self.page(self.second)["message"]["html"], '<p><b>Message B</b> — café</p>')

    def test_keyboard_selection_and_small_window_retain_accessible_controls(self):
        self.select(self.ids[0])
        self.tab.page_list.setFocus()
        QtTest.QTest.keyClick(self.tab.page_list, QtCore.Qt.Key_Right)
        self.pump()
        self.assertEqual(self.session.selected_page_id, self.first)
        for width, height in ((700, 520), (1200, 800)):
            self.tab.resize(width, height)
            self.pump()
            self.assertTrue(self.tab.page_list.isVisible())
            self.assertGreater(self.tab.page_list.width(), 0)
            self.assertTrue(self.tab.name_edit.isVisible())
            self.assertEqual(self.list_ids(), self.ids)

    def test_same_path_replacement_refreshes_only_valid_media(self):
        self.select(self.first)
        path = self.tab.artwork.resolve_asset(self.first)
        before = self.session.project.to_dict()
        old_key = self.tab._preview_key
        old_image = self.tab.card._source_pixmap.toImage()
        path.write_bytes(b"partial image write")
        self.tab.refresh_cards()
        self.assertEqual(self.tab._preview_key, old_key)
        self.assertEqual(self.tab.card._source_pixmap.toImage(), old_image)
        self.assertIn("preview out of date", self.row_text(self.first))
        Image.new("RGB", (120, 180), "yellow").save(path)
        self.tab.refresh_cards()
        self.assertNotEqual(self.tab._preview_key, old_key)
        self.assertEqual(self.tab.card._source_pixmap.toImage().pixelColor(2, 2).name(), "#ffff00")
        self.assertEqual(self.session.project.to_dict(), before)

    def test_pending_name_save_failure_blocks_navigation(self):
        self.select(self.first)
        self.tab.name_edit.setText("Last name edit")
        self.tab.name_edit.textEdited.emit("Last name edit")
        with mock.patch.object(self.session, "save", side_effect=OSError("synthetic failure")):
            self.tab.page_list.setCurrentRow(self.list_ids().index(self.second))
        self.assertEqual(self.session.selected_page_id, self.first)
        self.assertTrue(self.tab._name_dirty)
        self.assertEqual(self.tab.name_edit.text(), "Last name edit")
        self.assertTrue(self.tab.flush_pending_edits())

    def test_external_selection_preserves_pending_name_on_its_original_page(self):
        self.select(self.first)
        self.tab.name_edit.setText("Edited before external selection")
        self.tab.name_edit.textEdited.emit("Edited before external selection")
        self.session.select_page(self.second)
        self.pump()
        self.assertEqual(self.page(self.first)["name"], "Edited before external selection")
        self.assertEqual(self.page(self.second)["name"], "Repeated Letter")
        self.assertEqual(self.tab.name_edit.text(), "Repeated Letter")
        stored = active_project_repository(self.root).load()
        self.assertEqual(stored.project.get_page(self.first)["name"], "Edited before external selection")


if __name__ == "__main__":
    unittest.main()
