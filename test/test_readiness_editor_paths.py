from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

import generate
from Editor import Editor
from Forge_Tab import ForgeTab, ReadinessWindow
from config import CONTROL_FILES, REQUIRED_SLIDES
from project_paths import ProjectPathError, ProjectPathResolver
from project_store import ProjectStore
from readiness import ReadinessResult, evaluate_readiness
from sound_model import TrackRecord
from sound_tab import ArchiveDialog


class ReadinessEditorAndProjectPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_readiness_panel_is_frameless_tool_closed_only_by_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = ReadinessWindow(Path(directory))
            self.assertEqual(panel.windowTitle(), "")
            self.assertTrue(panel.isWindow())
            self.assertTrue(panel.windowFlags() & QtCore.Qt.Tool)
            self.assertTrue(panel.windowFlags() & QtCore.Qt.FramelessWindowHint)
            self.assertGreater(panel.maximumWidth(), 520)
            panel.refresh(evaluate_readiness(directory))
            self.assertGreater(panel.height(), panel.minimumHeight())
            self.assertFalse(panel.isVisible())
            panel.show()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())
            panel.close()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())
            panel.hide()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())
            panel.shutdown()

    def test_complete_readiness_closes_panel_and_preview_selector_is_bright(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = ForgeTab(Path(directory))
            ready = ReadinessResult(
                items=(),
                completion_percentage=100,
                status="Ready",
            )
            tab.readiness_window.show()
            self.app.processEvents()
            with mock.patch("Forge_Tab.evaluate_readiness", return_value=ready):
                tab.refresh_readiness()
            self.assertFalse(tab.readiness_window.isVisible())

            with mock.patch.object(tab, "refresh_readiness", return_value=ready):
                tab.show_readiness_window()
            self.assertFalse(tab.readiness_window.isVisible())
            self.assertIn("#dffbff", tab.preview_format_label.styleSheet())
            self.assertGreaterEqual(
                tab.preview_mode.itemDelegate().sizeHint(
                    QtWidgets.QStyleOptionViewItem(),
                    tab.preview_mode.model().index(0, 0),
                ).height(),
                48,
            )
            tab.close()

    def test_duplicate_project_ids_are_reported_and_repaired_without_merging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = ProjectPathResolver(root)
            recipient = resolver.registry.get_or_create("José Núñez")
            recipient_dir = resolver.resolve_recipient_directory(recipient.recipient_id)
            project_id = str(uuid.uuid4())
            first = recipient_dir / "Letter One"
            second = recipient_dir / "Letter Two"
            for path, title in ((first, "Letter One"), (second, "Letter Two")):
                path.mkdir(parents=True)
                (path / "lettersmith-metadata.json").write_text(
                    json.dumps(
                        {
                            "project_id": project_id,
                            "recipient_id": recipient.recipient_id,
                            "recipient_name": recipient.display_name,
                            "recipient_title": title,
                            "letter_title": title,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(ProjectPathError, "Letter One") as raised:
                resolver.resolve_project_directory(project_id, recipient_id=recipient.recipient_id)
            self.assertIn("Letter Two", str(raised.exception))

            context = resolver.context_from_settings(
                {
                    "project_id": project_id,
                    "recipient_id": recipient.recipient_id,
                    "recipient_name": recipient.display_name,
                    "recipient_title": "Letter One",
                }
            )
            self.assertEqual(context.project_directory, first.resolve())
            self.assertEqual(
                resolver._metadata_project_id(first),
                project_id,
            )
            self.assertNotEqual(
                resolver._metadata_project_id(second),
                project_id,
            )
            self.assertTrue(
                any(second.glob("lettersmith-metadata.json.backup-*"))
            )

    def test_failed_project_id_rewrite_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = ProjectPathResolver(root)
            recipient = resolver.registry.get_or_create("Amina O'Connor")
            recipient_dir = resolver.resolve_recipient_directory(recipient.recipient_id)
            project_id = str(uuid.uuid4())
            active = recipient_dir / "Current"
            copy = recipient_dir / "Copied"
            for path in (active, copy):
                path.mkdir(parents=True)
                (path / "lettersmith-metadata.json").write_text(
                    json.dumps({"project_id": project_id, "recipient_title": path.name}),
                    encoding="utf-8",
                )
            before = (copy / "lettersmith-metadata.json").read_text(encoding="utf-8")
            with mock.patch("project_paths.safe_write_json", side_effect=OSError("read-only")):
                with self.assertRaises(ProjectPathError):
                    resolver.repair_duplicate_project_ids(active_project_directory=active)
            self.assertEqual(
                (copy / "lettersmith-metadata.json").read_text(encoding="utf-8"),
                before,
            )

    def test_project_store_uses_output_projects_and_migrates_old_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "projects" / "old-project"
            legacy.mkdir(parents=True)
            (legacy / "project.json").write_text(
                json.dumps({"name": "Old"}),
                encoding="utf-8",
            )
            store = ProjectStore(root)
            self.assertEqual(store.projects_dir, (root / "output" / "projects").resolve())
            self.assertTrue((store.projects_dir / "old-project" / "project.json").is_file())
            self.assertFalse((root / "projects").exists())

    def _make_editor(self, preset: str) -> tuple[Editor, tempfile.TemporaryDirectory[str]]:
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name)
        (root / "settings.json").write_text(
            json.dumps({"message_overlay_preset": preset}),
            encoding="utf-8",
        )
        host = QtWidgets.QWidget()
        host.project_root = root
        editor = Editor("<p>Letter</p>", parent=host)
        editor._host_for_test = host
        return editor, holder

    def test_editor_background_and_font_controls_stay_open(self) -> None:
        editor, holder = self._make_editor("black")
        try:
            editor.show()
            self.app.processEvents()
            for control in (
                editor.font_controls,
                editor.font_combo,
                editor.font_size_down,
                editor.font_size_spin,
                editor.font_size_up,
            ):
                self.assertTrue(control.isVisible())
                self.assertGreater(control.width(), 0)
            self.assertIn("background-color:#000000", editor.editor.styleSheet())
            editor.editor.selectAll()
            editor.set_font_size(24)
            self.assertEqual(round(editor.editor.textCursor().charFormat().fontPointSize()), 24)
            editor.font_size_spin.setFocus()
            event = QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Return,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(editor.font_size_spin, event)
            self.assertFalse(editor._closing)
        finally:
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_save_does_not_close_and_reentrant_save_is_ignored(self) -> None:
        editor, holder = self._make_editor("clear")
        try:
            editor._last_persisted_html = "different"
            def save_and_reenter(*_args, **_kwargs):
                editor._save_only()

            with mock.patch.object(
                editor.project_save_service,
                "save_message",
                side_effect=save_and_reenter,
            ) as save:
                editor._save_only()
            self.assertFalse(editor._closing)
            self.assertEqual(save.call_count, 1)
            self.assertIn("background-color:transparent", editor.editor.styleSheet())
        finally:
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_font_step_controls_preserve_selection_and_boundaries(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setHtml("<p>Alpha Beta</p>")
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor.set_font_size(16)

            editor.font_size_up.click()
            selected = editor.editor.textCursor()
            self.assertEqual((selected.selectionStart(), selected.selectionEnd()), (0, 5))
            self.assertEqual(round(selected.charFormat().fontPointSize()), 17)

            editor.font_size_down.click()
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                16,
            )

            editor.font_size_spin.stepUp()
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                17,
            )
            editor.font_size_spin.stepDown()
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                16,
            )

            editor.font_size_spin.lineEdit().setText("24")
            enter = QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Return,
                QtCore.Qt.NoModifier,
            )
            QtWidgets.QApplication.sendEvent(editor.font_size_spin.lineEdit(), enter)
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                24,
            )
            self.assertFalse(editor._closing)

            target_font = QtGui.QFont("LetterSmith Test Font")
            editor.font_combo.currentFontChanged.emit(target_font)
            self.app.processEvents()
            self.assertEqual(
                editor.editor.textCursor().charFormat().fontFamilies()[0],
                target_font.family(),
            )

            editor.font_size_spin.setValue(100)
            self.assertFalse(editor.font_size_up.isEnabled())
            self.assertTrue(editor.font_size_down.isEnabled())
            editor.font_size_spin.setValue(1)
            self.assertTrue(editor.font_size_up.isEnabled())
            self.assertFalse(editor.font_size_down.isEnabled())
            self.assertFalse(editor.font_size_spin.keyboardTracking())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_format_actions_preserve_text_structure_and_other_styles(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setHtml(
                '<p><span style="font-size:20pt;color:#ff0000">Alpha</span></p>'
                '<p><span style="font-size:12pt;color:#0000ff">Beta</span></p>'
            )
            before_text = editor.editor.toPlainText()
            before_blocks = editor.editor.document().blockCount()
            editor.editor.selectAll()
            editor.act_bold.trigger()
            self.assertTrue(editor.act_bold.isChecked())

            document = editor.editor.document()
            alpha = QtGui.QTextCursor(document)
            alpha.setPosition(0)
            alpha.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            beta = QtGui.QTextCursor(document)
            beta.setPosition(6)
            beta.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            self.assertEqual(round(alpha.charFormat().fontPointSize()), 20)
            self.assertEqual(round(beta.charFormat().fontPointSize()), 12)
            self.assertEqual(alpha.charFormat().foreground().color().name(), "#ff0000")
            self.assertEqual(beta.charFormat().foreground().color().name(), "#0000ff")
            self.assertTrue(alpha.charFormat().font().bold())
            self.assertTrue(beta.charFormat().font().bold())

            editor.editor.selectAll()
            editor.act_clear_formatting.trigger()
            self.assertEqual(editor.editor.toPlainText(), before_text)
            self.assertEqual(editor.editor.document().blockCount(), before_blocks)
            self.assertTrue(editor.editor.textCursor().hasSelection())
            self.assertFalse(editor.act_bold.isChecked())

            editor.spacing_actions[1.5].trigger()
            self.assertTrue(editor.spacing_actions[1.5].isChecked())
            editor.alignment_actions["center"].trigger()
            self.assertTrue(editor.alignment_actions["center"].isChecked())

            cursor = editor.editor.textCursor()
            cursor.clearSelection()
            cursor.movePosition(QtGui.QTextCursor.End)
            cursor.insertText("!")
            self.assertTrue(editor.act_undo.isEnabled())
            editor.act_undo.trigger()
            self.assertTrue(editor.act_redo.isEnabled())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_editor_toolbar_failures_are_contained(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            with mock.patch.object(QtWidgets.QMessageBox, "warning") as warning:
                editor._run_editor_action(
                    "Injected failure",
                    lambda: (_ for _ in ()).throw(RuntimeError("injected")),
                )
            warning.assert_called_once()
            error_log = editor.project_root / "editor_error.log"
            self.assertTrue(error_log.is_file())
            log_text = error_log.read_text(encoding="utf-8")
            self.assertIn("editor action: Injected failure", log_text)
            self.assertIn("RuntimeError: injected", log_text)
            self.assertFalse(editor._closing)
            self.assertTrue(editor.btn_save.isEnabled())
            self.assertTrue(editor.btn_close.isEnabled())
            self.assertTrue(editor.btn_save_close.isEnabled())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_generated_viewer_embeds_the_selected_font(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pages = root / "gallery/user/pages"
            controls = root / "gallery/user/card/controls"
            fonts = root / "gallery/user/fonts"
            for folder in (pages, controls, fonts):
                folder.mkdir(parents=True, exist_ok=True)

            image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGBA8888)
            image.fill(QtGui.QColor("white"))
            for name in REQUIRED_SLIDES:
                self.assertTrue(image.save(str(pages / name)))
            for name in CONTROL_FILES:
                self.assertTrue(image.save(str(controls / name)))

            font_path = fonts / "Arcane_Font.ttf"
            header = struct.pack(">IHHHH", 0x00010000, 1, 0, 0, 0)
            table_record = struct.pack(">4sIII", b"OS/2", 0, 28, 10)
            font_path.write_bytes(header + table_record + (b"\0" * 10))

            play_dir = generate.generate_play_bundle(
                str(root),
                message_html=(
                    '<span style="font-family:\'Arcane\';font-size:24pt">'
                    "Letter</span>"
                ),
                seed_sfx=False,
            )
            styles = (play_dir / "styles.css").read_text(encoding="utf-8")
            index = (play_dir / "index.html").read_text(encoding="utf-8")
            state = json.loads(
                (play_dir / generate.BUILD_STATE_FILE).read_text(encoding="utf-8")
            )
            exported_files = state["font_export"]["files"]

            self.assertIn("@font-face", styles)
            self.assertIn("LetterSmithFont1", styles)
            self.assertIn("font-family:'LetterSmithFont1', 'Arcane'", index)
            self.assertEqual(state["font_export"]["embedded"], ["Arcane"])
            self.assertEqual(len(exported_files), 1)
            self.assertTrue(
                (play_dir / "gallery/fonts" / exported_files[0]).is_file()
            )

    def test_ultralink_button_requires_safe_selected_text(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setHtml("<p>Alpha Beta</p>")
            cursor = editor.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            editor.editor.setTextCursor(cursor)
            editor._update_ultralink_action()
            self.assertFalse(editor.btn_ultralink.isEnabled())

            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor._update_ultralink_action()
            self.assertTrue(editor.btn_ultralink.isEnabled())

            web_link = QtGui.QTextCharFormat()
            web_link.setAnchor(True)
            web_link.setAnchorHref("https://example.com")
            editor._apply_char_format(web_link)
            editor._update_ultralink_action()
            self.assertFalse(editor.btn_ultralink.isEnabled())
            self.assertIn("Remove the web link", editor.btn_ultralink.toolTip())

            with mock.patch.object(QtWidgets.QMessageBox, "information") as info:
                editor.open_ultralink_dialog()
            info.assert_called_once()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_music_archive_exposes_only_remaining_actions(self) -> None:
        class Library(QtCore.QObject):
            changed = QtCore.Signal()

            def __init__(self) -> None:
                super().__init__()
                self.project_root = Path(tempfile.gettempdir())
                self.record = TrackRecord(
                    track_id="track-1",
                    content_hash="hash",
                    display_title="Song",
                    original_name="song.mp3",
                    original_file="song.mp3",
                    processed_file="song.mp3",
                    duration_seconds=1.0,
                    added_at="2026-01-01T00:00:00+00:00",
                )

            def all_records(self, _sort: str) -> list[TrackRecord]:
                return [self.record]

            def get(self, _track_id: str) -> TrackRecord:
                return self.record

            def path_for(self, _track_id: str) -> None:
                return None

            def rename_display_title(self, _track_id: str, title: str) -> None:
                self.record.display_title = title

        library = Library()
        dialog = ArchiveDialog(
            library,
            lambda: set(),
            lambda _track_id: True,
            multi_select=False,
        )
        try:
            labels = {button.text() for button in dialog.findChildren(QtWidgets.QPushButton)}
            self.assertEqual(labels & {"Preview", "Show Original", "Repair Archive", "Close"}, set())
            self.assertIn("Rename Title", labels)
            self.assertIn("Delete", labels)
            self.assertIn("Use Selected", labels)
            self.assertFalse(dialog.rename_btn.isEnabled())
            dialog.table.selectRow(0)
            self.app.processEvents()
            self.assertTrue(dialog.rename_btn.isEnabled())
            self.assertTrue(dialog.delete_btn.isEnabled())
            self.assertTrue(dialog.choose_btn.isEnabled())
        finally:
            dialog.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
