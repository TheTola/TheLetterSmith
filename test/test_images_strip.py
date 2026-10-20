"""Interaction regressions for the ordered, horizontal Images strip."""
import unittest
from unittest import mock
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from project_model import MAX_PAGES, PAGE_TYPES, create_project
from recent_media import (clear_type_image_history, recent_type_images,
                          remember_type_image, remember_media, recent_media)
import test_images_workspace as fixture


class ImagesStripTests(unittest.TestCase):
    setUpClass = classmethod(fixture.ImagesWorkspaceTests.setUpClass.__func__)
    setUp = fixture.ImagesWorkspaceTests.setUp
    tearDown = fixture.ImagesWorkspaceTests.tearDown
    pump = fixture.ImagesWorkspaceTests.pump
    wait_for = fixture.ImagesWorkspaceTests.wait_for
    image = fixture.ImagesWorkspaceTests.image
    page = fixture.ImagesWorkspaceTests.page
    select = fixture.ImagesWorkspaceTests.select
    list_ids = fixture.ImagesWorkspaceTests.list_ids

    def test_import_history_retains_each_type_and_original_gif_after_project_switch(self):
        gif = fixture.ImagesWorkspaceTests.gif(self)
        original = gif.read_bytes()
        for row in (0, 1, 2, 4):
            page = self.session.project.pages[row]
            self.assertTrue(self.tab.import_image(page["id"], str(gif)))
            self.wait_for(lambda: self.tab._image_import_thread is None)
            history = recent_type_images(self.root, page["type"])
            self.assertEqual(len(history), 1)
            self.assertEqual(Path(history[0][0]).read_bytes(), original)
        gif.unlink()
        self.session.replace(create_project(), asset_root=self.root)
        for kind in PAGE_TYPES:
            history = recent_type_images(self.root, kind)
            self.assertEqual(Path(history[0][0]).read_bytes(), original)
            with Image.open(history[0][0]) as retained:
                self.assertEqual(retained.n_frames, 2)

    def test_ten_recent_images_are_shared_by_type_across_projects_and_clear_keeps_files(self):
        originals = []
        for number in range(12):
            source = self.root / f"recent-{number}.png"
            Image.new("RGB", (24, 36), (number*20, 20, 80)).save(source)
            originals.append(source)
            for kind in PAGE_TYPES:
                remember_type_image(self.root, kind, source, f"{kind}-{number}")
        for kind in PAGE_TYPES:
            self.assertEqual([label for _, label in recent_type_images(self.root, kind)],
                             [f"{kind}-{i}" for i in reversed(range(2, 12))])
        remember_type_image(self.root, "letter", originals[5], "letter-5")
        self.assertEqual(len(recent_type_images(self.root, "letter")), 10)
        self.assertEqual(recent_type_images(self.root, "letter")[0][1], "letter-5")
        self.assertEqual(recent_type_images(self.root, "cover")[0][1], "cover-11")
        before = {kind: recent_type_images(self.root, kind) for kind in PAGE_TYPES}
        self.session.replace(create_project(("letter", "letter", "illustration")), asset_root=self.root)
        self.assertEqual(before, {kind: recent_type_images(self.root, kind) for kind in PAGE_TYPES})
        remember_media(self.root, self.session.project.project_id, "music", "track", "Track")
        files = {Path(path): Path(path).read_bytes() for entries in before.values() for path, _ in entries}
        files.update({p: p.read_bytes() for p in originals})
        project = self.session.project.to_dict()
        clear_type_image_history(self.root)
        for kind in PAGE_TYPES:
            self.assertEqual(recent_type_images(self.root, kind), ())
        self.assertEqual({p: p.read_bytes() for p in files}, files)
        self.assertEqual(self.session.project.to_dict(), project)
        self.assertEqual(recent_media(self.root, self.session.project.project_id, "music"), (("track", "Track"),))

    def test_recent_images_deduplicate_content_without_collapsing_same_filenames(self):
        original = self.image("same.png", "gold")
        renamed = self.root / "renamed.png"
        renamed.write_bytes(original.read_bytes())
        reencoded = self.root / "different-encoding.png"
        metadata = PngInfo()
        metadata.add_text("Description", "Same artwork, different metadata")
        with Image.open(original) as image:
            image.save(reencoded, compress_level=0, pnginfo=metadata)
        self.assertNotEqual(original.read_bytes(), reencoded.read_bytes())
        other_folder = self.root / "other"
        other_folder.mkdir()
        different = other_folder / original.name
        Image.new("RGB", (160, 240), "navy").save(different)
        expected = {}
        for kind in PAGE_TYPES:
            with self.subTest(kind=kind):
                remember_type_image(self.root, kind, original, "First")
                remember_type_image(self.root, kind, different, "Different artwork")
                for source in (original, renamed, reencoded, renamed, original):
                    remember_type_image(self.root, kind, source, "Used again")
                    history = recent_type_images(self.root, kind)
                    self.assertEqual([label for _, label in history], ["Used again", "Different artwork"])
                    with Image.open(history[0][0]) as image:
                        self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 215, 0))
                    with Image.open(history[1][0]) as image:
                        self.assertEqual(image.convert("RGB").getpixel((0, 0)), (0, 0, 128))
                expected[kind] = history
                self.assertEqual({key: recent_type_images(self.root, key) for key in expected}, expected)
        self.session.replace(create_project(), asset_root=self.root)
        self.assertEqual({kind: recent_type_images(self.root, kind) for kind in PAGE_TYPES}, expected)
        self.assertTrue(all(path.is_file() for path in (original, renamed, reencoded, different)))

    def test_existing_duplicate_history_is_filtered_without_rewriting_or_deleting_files(self):
        import json
        from recent_media import _history_path, _TYPE_HISTORY_ID
        original = self.image("original.png", "gold")
        duplicate = self.image("duplicate.png", "gold")
        older = self.image("older.png", "navy")
        values = [{"value": str(path), "label": label} for path, label in
                  ((duplicate, "Newest"), (original, "Repeated"), (older, "Older"))]
        history_file = _history_path(self.root)
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps({_TYPE_HISTORY_ID: {"image:cover": values}}), encoding="utf-8")
        saved = history_file.read_bytes()
        self.assertEqual(recent_type_images(self.root, "cover"),
                         ((str(duplicate), "Newest"), (str(older), "Older")))
        self.assertEqual(history_file.read_bytes(), saved)
        self.assertTrue(all(path.is_file() for path in (original, duplicate, older)))
        Image.new("RGB", (160, 240), "green").save(duplicate)
        self.assertEqual(recent_type_images(self.root, "cover"),
                         ((str(duplicate), "Newest"), (str(original), "Repeated"), (str(older), "Older")))
        self.assertEqual(history_file.read_bytes(), saved)

    def test_animation_deduplication_uses_every_frame_and_playback_timing(self):
        red, blue, green = (Image.new("RGB", (24, 36), color) for color in ("red", "blue", "green"))
        sources = []
        for name, last, duration, loop in (("first.gif", blue, 90, 0),
                                           ("different-frame.gif", green, 90, 0),
                                           ("different-timing.gif", blue, 180, 0),
                                           ("different-loop.gif", blue, 90, 2)):
            path = self.root / name
            red.save(path, save_all=True, append_images=[last], duration=duration, loop=loop)
            sources.append(path)
            remember_type_image(self.root, "illustration", path, name)
        duplicate = self.root / "renamed.gif"
        red.save(duplicate, save_all=True, append_images=[blue], duration=90, loop=0,
                 comment=b"Same animation with different metadata")
        self.assertNotEqual(duplicate.read_bytes(), sources[0].read_bytes())
        remember_type_image(self.root, "illustration", duplicate, "Used again")
        history = recent_type_images(self.root, "illustration")
        self.assertEqual([label for _, label in history],
                         ["Used again", "different-loop.gif", "different-timing.gif", "different-frame.gif"])
        with Image.open(history[0][0]) as image:
            self.assertEqual(image.n_frames, 2)

    def test_every_same_type_page_uses_shared_history_then_its_three_stock_images(self):
        from Image_tab import STOCK_IMAGE_FILES
        from page_layout import PAGE_INDICES
        from project_paths import application_paths
        self.session.replace(create_project(tuple(PAGE_TYPES) * 2), asset_root=self.root)
        for kind, definition in PAGE_TYPES.items():
            directory = application_paths(self.root).stock_images_root / definition.artwork_template
            directory.mkdir(parents=True, exist_ok=True)
            for name in STOCK_IMAGE_FILES[PAGE_INDICES[definition.artwork_template]]:
                Image.new("RGB", (24, 36), "navy").save(directory / name)
            remember_type_image(self.root, kind, self.image(), kind)
        expected = {kind: recent_type_images(self.root, kind) for kind in PAGE_TYPES}
        for cleared in (False, True):
            if cleared:
                clear_type_image_history(self.root)
            for page in self.session.project.pages:
                self.select(page["id"])
                # Old per-page selections must not reappear after Command.
                remember_media(self.root, self.session.project.project_id, f"image:{page['id']}",
                               str(self.image()), "Old private selection")
                with mock.patch("Image_tab.StockImageDialog") as dialog:
                    dialog.return_value.exec.return_value = QtWidgets.QDialog.Rejected
                    self.tab._stock_selected()
                if cleared:
                    self.assertEqual(len(dialog.call_args.args[1]), 3)
                    self.assertEqual(dialog.call_args.kwargs["recent_paths"], ())
                else:
                    self.assertEqual(dialog.call_args.args[1], ())
                    self.assertEqual(dialog.call_args.kwargs["recent_paths"],
                                     tuple(Path(p) for p, _ in expected[page["type"]]))

    def test_single_click_opens_picker_and_keyboard_can_choose_tenth_image(self):
        from Image_tab import StockImageDialog
        source = self.image()
        for i in range(10):
            Image.new("RGB", (24, 36), (i*20, 20, 100)).save(source)
            remember_type_image(self.root, "letter", source, f"Choice {i}")
        choices = recent_type_images(self.root, "letter")
        with Image.open(choices[9][0]) as expected:
            expected_size, expected_pixels = expected.size, expected.convert("RGB").tobytes()
        before = self.page(self.second)
        chosen = []
        class AutoChoose(StockImageDialog):
            def showEvent(dialog, event):
                super().showEvent(event)
                def pick():
                    view = dialog._recent_images
                    self.assertEqual(view.count(), 10)
                    view.setFocus()
                    QtTest.QTest.keyClick(view, QtCore.Qt.Key_End)
                    self.assertEqual(view.currentRow(), 9)
                    chosen.append(view.currentItem().data(QtCore.Qt.UserRole))
                    QtTest.QTest.keyClick(view, QtCore.Qt.Key_Return)
                QtCore.QTimer.singleShot(0, dialog, pick)
        view = self.tab.page_list
        view.scrollToItem(view.item(1))
        self.pump()
        with mock.patch("Image_tab.StockImageDialog", AutoChoose):
            QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton,
                                   pos=view.visualItemRect(view.item(1)).center())
            self.wait_for(lambda: bool(chosen) and self.tab._image_import_thread is None)
        self.assertEqual(chosen, [choices[9][0]])
        self.assertEqual([label for _, label in recent_type_images(self.root, "letter")],
                         ["Choice 0", *[f"Choice {i}" for i in reversed(range(1, 10))]])
        with Image.open(self.tab.artwork.resolve_asset(self.first)) as imported:
            self.assertEqual(imported.size, expected_size)
            self.assertEqual(imported.convert("RGB").tobytes(), expected_pixels)
        self.assertEqual(self.page(self.second), before)

    def test_delayed_click_does_not_open_in_replaced_session(self):
        view = self.tab.page_list
        with mock.patch.object(self.tab, "_stock_selected") as picker:
            QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.LeftButton,
                                   pos=view.visualItemRect(view.item(0)).center())
            previous = self.session.project
            self.session.replace(create_project(), asset_root=self.root)
            self.session.replace(previous, asset_root=self.root)
            view._choice_timer.stop()
            view._choose_after_click()
            picker.assert_not_called()

    def wheel(self, angle=0, pixel=0, horizontal=False):
        view = self.tab.page_list.viewport()
        event = QtGui.QWheelEvent(
            QtCore.QPointF(view.rect().center()), QtCore.QPointF(view.mapToGlobal(view.rect().center())),
            QtCore.QPoint(pixel, 0) if horizontal else QtCore.QPoint(0, pixel),
            QtCore.QPoint(angle, 0) if horizontal else QtCore.QPoint(0, angle),
            QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(view, event)
        self.pump()

    def test_ten_pages_stay_in_one_row_at_all_window_sizes(self):
        while len(self.session.project.pages) < MAX_PAGES:
            self.tab.add_page("illustration")
        for size in ((1200, 760), (900, 480), (560, 700)):
            self.tab.resize(*size)
            self.pump()
            view = self.tab.page_list
            rects = [view.visualItemRect(view.item(i)) for i in range(view.count())]
            self.assertEqual(len({r.y() for r in rects}), 1)
            self.assertTrue(all(a.right() < b.left() for a, b in zip(rects, rects[1:])))
            self.assertEqual(view.verticalScrollBar().maximum(), 0)
            self.assertGreater(view.horizontalScrollBar().maximum(), 0)

    def test_vertical_wheel_and_touchpad_scroll_horizontally_and_stop_at_ends(self):
        bar = self.tab.page_list.horizontalScrollBar()
        original = self.session.selected_page_id
        bar.setValue(0)
        self.wheel(angle=-120)
        self.assertGreater(bar.value(), 0)
        offset = bar.value()
        self.tab.refresh_cards()
        self.pump()
        self.assertEqual(bar.value(), offset)
        self.wheel(pixel=-19, horizontal=True)
        self.assertEqual(bar.value(), offset+19)
        self.wheel(angle=120)
        self.assertLess(bar.value(), offset)
        bar.setValue(bar.maximum())
        self.wheel(angle=-120)
        self.assertEqual(bar.value(), bar.maximum())
        bar.setValue(0)
        self.wheel(angle=120)
        self.assertEqual(bar.value(), 0)
        self.assertEqual(self.session.selected_page_id, original)

    def test_double_click_import_keeps_clicked_page_when_selection_changes_in_dialog(self):
        navigator = self.tab.page_list
        clicked = self.ids[1]
        original_asset = self.page(clicked)['artwork']['asset']
        previous = self.page(self.ids[-1])["artwork"]
        path = self.image()
        def pick(*args):
            self.session.select_page(self.ids[-1])
            return str(path), "Images"
        point = navigator.visualItemRect(navigator.item(1)).center()
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName", side_effect=pick) as dialog:
            QtTest.QTest.mouseDClick(navigator.viewport(), QtCore.Qt.LeftButton, pos=point)
            dialog.assert_called_once()
            self.wait_for(lambda: self.tab._image_import_thread is None)
        self.assertNotEqual(self.page(clicked)["artwork"]["asset"], original_asset)
        with Image.open(self.tab.artwork.resolve_asset(clicked)) as imported, Image.open(path) as source:
            self.assertEqual(imported.size, source.size)
            self.assertEqual(imported.convert('RGB').tobytes(), source.convert('RGB').tobytes())
        self.assertEqual(self.page(self.ids[-1])["artwork"], previous)
        self.assertEqual(self.session.selected_page_id, self.ids[-1])

    def test_context_clear_targets_original_page_and_preserves_message_and_prompt(self):
        self.select(self.first)
        before = self.page(self.first)
        other = self.page(self.second)
        menu = self.tab._page_menu(self.first)
        actions = {action.text(): action for action in menu.actions()}
        for label in ("Import image…", "Stock & recent images…", "Crop image…",
                      "Clear page artwork", "Delete page…", "Add page", "Prompt Writer",
                      "Edit letter text", "Move left", "Move right"):
            self.assertIn(label, actions)
        self.assertIn("Duplicate page", actions)
        self.assertIn("Open Artwork Folder", actions)
        self.select(self.second)
        actions["Clear page artwork"].trigger()
        self.assertIsNone(self.page(self.first)["artwork"]["asset"])
        self.assertEqual(self.page(self.first)["message"], before["message"])
        self.assertEqual(self.page(self.first)["prompt"], before["prompt"])
        self.assertEqual(self.page(self.second), other)
        menu.deleteLater()

    def test_old_context_menu_cannot_act_on_replaced_project(self):
        self.select(self.ids[0])
        menu = self.tab._page_menu(self.ids[0])
        action = next(a for a in menu.actions() if a.text() == "Import image…")
        self.session.replace(create_project(), asset_root=self.root)
        with mock.patch.object(self.tab, "_browse_selected") as browse:
            action.trigger()
            browse.assert_not_called()
        menu.deleteLater()

    def test_move_arrows_flank_artwork_and_move_only_selected_page(self):
        self.select(self.ids[1])
        self.pump()
        from images_grid import _artwork_rect
        rect = _artwork_rect(self.tab.page_list.visualItemRect(self.tab.page_list.currentItem()))
        left, right = self.tab.up_button, self.tab.down_button
        self.assertTrue(left.isVisible())
        self.assertTrue(right.isVisible())
        self.assertLess(left.geometry().right(), rect.left())
        self.assertGreater(right.geometry().left(), rect.right())
        before = self.page(self.ids[1])
        QtTest.QTest.mouseClick(right, QtCore.Qt.LeftButton)
        self.pump()
        self.assertEqual(self.list_ids()[2], self.ids[1])
        self.assertEqual(self.session.selected_page_id, self.ids[1])
        self.assertEqual(self.page(self.ids[1])["artwork"], before["artwork"])

    def test_header_and_minimal_surface_remain_clear_with_empty_pages(self):
        self.session.replace(create_project(), asset_root=self.root)
        self.pump()
        strip_y = self.tab.page_list.y()
        for widget in (self.tab.pwrite_fab, self.tab.name_edit, self.tab.type_combo):
            rect = QtCore.QRect(widget.mapTo(self.tab, QtCore.QPoint()), widget.size())
            self.assertLess(rect.bottom(), strip_y)
        self.assertLess(self.tab.pwrite_fab.x(), self.tab.name_edit.x())
        self.assertTrue(self.tab.add_button.isVisibleTo(self.tab))
        self.assertFalse(any(button.text() == "Page actions"
                             for button in self.tab.findChildren(QtWidgets.QAbstractButton)))
        for button in (self.tab.import_button, self.tab.stock_button, self.tab.crop_button,
                       self.tab.duplicate_button, self.tab.delete_button, self.tab.more_button,
                       self.tab.undo_button):
            self.assertFalse(button.isVisibleTo(self.tab))
        self.assertFalse(self.tab.limit_label.isVisibleTo(self.tab))

    def test_page_fields_are_compact_inline_and_leave_room_for_artwork_launcher(self):
        for width, height in ((1200, 760), (900, 480), (560, 700)):
            self.tab.resize(width, height)
            self.pump()
            name = self.tab.name_edit
            self.assertLessEqual(name.width(), name.fontMetrics().horizontalAdvance('Optional page name') + 40)
            for label, field in zip(self.tab._field_labels, (name, self.tab.type_combo)):
                label_rect = QtCore.QRect(label.mapTo(self.tab, QtCore.QPoint()), label.size())
                field_rect = QtCore.QRect(field.mapTo(self.tab, QtCore.QPoint()), field.size())
                self.assertLess(label_rect.right(), field_rect.left())
                self.assertLessEqual(abs(label_rect.center().y()-field_rect.center().y()), 2)
                self.assertTrue(self.tab.rect().contains(field_rect))
                self.assertTrue(label.font().bold())
                self.assertTrue(field.font().bold())
            self.assertGreaterEqual(self.tab.pwrite_fab.width(), 128)
            self.assertLess(self.tab.type_combo.mapTo(self.tab, QtCore.QPoint()).x(), width-160)
            self.assertLessEqual(abs(self.tab._identity_panel.geometry().center().x()
                                     - self.tab.rect().center().x()), 1)
            self.assertEqual(self.tab.pwrite_fab.y(), self.tab.layout().contentsMargins().top())

    def test_visible_add_page_menu_inserts_after_selection_and_respects_limit(self):
        self.select(self.first)
        before = self.list_ids()
        action = next(action for action in self.tab.add_button.menu().actions()
                      if action.text().startswith("Illustration"))
        action.trigger()
        self.pump()
        added = self.session.selected_page_id
        self.assertEqual(self.page(added)["type"], "illustration")
        self.assertEqual(self.list_ids().index(added), before.index(self.first) + 1)
        while len(self.session.project.pages) < MAX_PAGES:
            self.tab.add_page("illustration")
        self.assertTrue(self.tab.add_button.isVisibleTo(self.tab))
        self.assertFalse(self.tab.add_button.isEnabled())

    def test_short_strip_is_centered_and_expands_for_scrolling(self):
        self.session.replace(create_project(), asset_root=self.root)
        for width in (1900, 900, 1900):
            self.tab.resize(width, 760)
            self.pump()
            view = self.tab.page_list
            first = view.visualItemRect(view.item(0))
            last = view.visualItemRect(view.item(view.count()-1))
            if view.horizontalScrollBar().maximum() == 0:
                center = view.viewport().mapTo(self.tab, first.united(last).center())
                self.assertLessEqual(abs(center.x()-self.tab.rect().center().x()), 1)
            else:
                self.assertEqual(view.viewportMargins().left(), 0)

    def test_context_add_at_limit_reports_limit_without_losing_pages(self):
        while len(self.session.project.pages) < MAX_PAGES:
            self.tab.add_page("illustration")
        ids = self.list_ids()
        menu = self.tab._page_menu(self.session.selected_page_id)
        submenu_action = next(a for a in menu.actions() if a.text() == "Add page")
        submenu = submenu_action.menu()
        submenu.actions()[0].trigger()
        self.assertEqual(self.list_ids(), ids)
        self.assertIn("maximum of 10", self.tab.status.text())
        menu.deleteLater()
