"""Interaction regressions for the ordered, horizontal Images strip."""
import unittest
from unittest import mock

from PIL import Image
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from project_model import MAX_PAGES, create_project
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
                      "Clear page artwork", "Delete page…", "Add page"):
            self.assertIn(label, actions)
        self.assertFalse(any("Duplicate" in name for name in actions))
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
        for button in (self.tab.import_button, self.tab.stock_button, self.tab.crop_button,
                       self.tab.duplicate_button, self.tab.delete_button, self.tab.more_button,
                       self.tab.add_button, self.tab.undo_button):
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
            self.assertGreaterEqual(self.tab.pwrite_fab.width(), 128)
            self.assertLess(self.tab.type_combo.mapTo(self.tab, QtCore.QPoint()).x(), width-160)

    def test_context_add_at_limit_reports_limit_without_losing_pages(self):
        while len(self.session.project.pages) < MAX_PAGES:
            self.tab.add_page("illustration")
        ids = self.list_ids()
        menu = self.tab._page_menu(self.session.selected_page_id)
        submenu = next(a.menu() for a in menu.actions() if a.text() == "Add page")
        submenu.actions()[0].trigger()
        self.assertEqual(self.list_ids(), ids)
        self.assertIn("maximum of 10", self.tab.status.text())
        menu.deleteLater()
