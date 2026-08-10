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

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

import generate
from Editor import Editor, UltralinkDialog
from message_html import ultralink_message_from_href
from Forge_Tab import ForgeTab, ReadinessWindow
from config import (
    CONTROL_FILES,
    REQUIRED_SLIDES,
    resolve_play_bundle_directory,
)
from project_paths import ProjectPathError, ProjectPathResolver
from project_save import ProjectNotReadyError, ProjectSaveService
from project_state import ProjectStateController
from readiness import (
    ReadinessResult,
    evaluate_project_save_eligibility,
    evaluate_readiness,
)
from settings_store import SettingsStore
from sound_model import (
    ProjectSoundState,
    TrackRecord,
    import_runtime_track,
    save_project_state,
)
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
            recipient_dir = resolver.resolve_autosave_recipient_directory(recipient.recipient_id)
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
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(ProjectPathError, "Letter One") as raised:
                resolver.resolve_autosave_directory(project_id, recipient_id=recipient.recipient_id)
            self.assertIn("Letter Two", str(raised.exception))

            context = resolver.context_from_settings(
                {
                    "project_id": project_id,
                    "recipient_id": recipient.recipient_id,
                    "recipient_name": recipient.display_name,
                    "recipient_title": "Letter One",
                }
            )
            self.assertEqual(context.autosave_directory, first.resolve())
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
            recipient_dir = resolver.resolve_autosave_recipient_directory(recipient.recipient_id)
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
                    resolver.repair_duplicate_autosave_ids(active_autosave_directory=active)
            self.assertEqual(
                (copy / "lettersmith-metadata.json").read_text(encoding="utf-8"),
                before,
            )

    def test_same_recipient_requires_a_unique_letter_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            resolver = ProjectPathResolver(root)
            recipient_dir = resolver.resolve_autosave_recipient_directory(
                identity.recipient_id
            )
            existing = recipient_dir / "Morning Joy"
            existing.mkdir(parents=True)
            (existing / "lettersmith-metadata.json").write_text(
                json.dumps(
                    {
                        "project_id": str(uuid.uuid4()),
                        "recipient_id": identity.recipient_id,
                        "recipient_name": "Amanda Miller",
                        "recipient_title": "Morning Joy",
                    }
                ),
                encoding="utf-8",
            )
            settings = {
                **identity.as_settings(),
                "recipient_title": "Morning Joy",
            }

            self.assertEqual(
                resolver.find_title_conflict(
                    identity.recipient_id,
                    "Morning Joy",
                    project_id=identity.project_id,
                ),
                existing.resolve(),
            )
            with self.assertRaisesRegex(
                ProjectPathError,
                "different letter title",
            ):
                resolver.context_from_settings(settings)
            SettingsStore(root).update_fields(
                {"recipient_title": "Morning Joy"}
            )
            title_item = next(
                item
                for item in evaluate_readiness(root).items
                if item.key == "title"
            )
            self.assertFalse(title_item.ready)
            self.assertIn("different letter title", title_item.detail)

    def test_project_save_requires_two_completed_tabs(self) -> None:
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
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(name.encode("ascii"))

            resolver = ProjectPathResolver(root)
            service = ProjectSaveService(
                root,
                state,
                resolver=resolver,
            )
            eligibility = evaluate_project_save_eligibility(root)
            self.assertEqual(eligibility.completed_tabs, ("images",))
            self.assertFalse(eligibility.can_save)
            context = resolver.context_from_settings(
                SettingsStore(root).snapshot()
            )
            service.copy_workspace_file(
                pages / "cover.png",
                Path("pages") / "cover.png",
            )
            self.assertFalse(context.autosave_directory.exists())

            message = root / "gallery" / "user" / "message" / "message.html"
            message.parent.mkdir(parents=True)
            message.write_text("<p>Good morning.</p>", encoding="utf-8")
            eligibility = evaluate_project_save_eligibility(root)
            self.assertEqual(
                eligibility.completed_tabs,
                ("images", "message"),
            )
            self.assertTrue(eligibility.can_save)

            saved = service.save_workspace_snapshot(reason="tab-switch")

            self.assertEqual(saved, context.autosave_directory)
            self.assertTrue((saved / "pages" / "cover.png").is_file())
            self.assertEqual(
                (saved / "message" / "message.html").read_text(
                    encoding="utf-8"
                ),
                "<p>Good morning.</p>",
            )
            metadata = json.loads(
                (saved / "lettersmith-metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                metadata["completed_tabs"],
                ["images", "message"],
            )
            self.assertEqual(metadata["autosave_reason"], "tab-switch")

    def test_title_change_renames_the_same_project_and_play_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            identity = state.establish_project(
                "Amani Hill",
                custom_capitalization=True,
            )
            settings = SettingsStore(root)
            settings.update_fields(
                {"recipient_title": "Perfection's Path"}
            )
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(b"image")
            message = root / "gallery" / "user" / "message" / "message.html"
            message.parent.mkdir(parents=True)
            message.write_text("<p>Letter</p>", encoding="utf-8")
            resolver = ProjectPathResolver(root)
            service = ProjectSaveService(root, state, resolver=resolver)

            original_project = service.save_workspace_snapshot(
                reason="tab-switch"
            )
            play_recipient = resolver.resolve_play_recipient_directory(
                identity.recipient_id
            )
            original_play = play_recipient / "Perfection's Path"
            original_play.mkdir(parents=True)
            (original_play / "index.html").write_text(
                "<title>Perfection's Path</title>",
                encoding="utf-8",
            )
            (original_play / "styles.css").write_text("", encoding="utf-8")
            (original_play / "script.js").write_text("", encoding="utf-8")
            (original_play / "lettersmith-build.json").write_text(
                json.dumps({"project_id": identity.project_id}),
                encoding="utf-8",
            )

            settings.update_fields(
                {"recipient_title": "The Path of Perfection"}
            )
            renamed_project = service.save_workspace_snapshot(
                reason="tab-switch"
            )
            renamed_play = play_recipient / "The Path of Perfection"

            self.assertFalse(original_project.exists())
            self.assertEqual(
                renamed_project.name,
                "The Path of Perfection",
            )
            self.assertEqual(
                json.loads(
                    (
                        renamed_project / "lettersmith-metadata.json"
                    ).read_text(encoding="utf-8")
                )["project_id"],
                identity.project_id,
            )
            self.assertFalse(original_play.exists())
            self.assertTrue(renamed_play.is_dir())
            play_metadata = json.loads(
                (renamed_play / "lettersmith-metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                play_metadata["project_id"],
                identity.project_id,
            )
            self.assertEqual(
                play_metadata["recipient_id"],
                identity.recipient_id,
            )
            self.assertEqual(
                play_metadata["recipient_title"],
                "The Path of Perfection",
            )
            self.assertEqual(
                settings.snapshot()["active_play_dir"],
                str(renamed_play.resolve()),
            )
            with self.assertRaises(FileExistsError):
                resolve_play_bundle_directory(
                    root,
                    recipient="Amani Hill",
                    title="The Path of Perfection",
                    project_id=str(uuid.uuid4()),
                )
            self.assertFalse(
                (play_recipient / "The Path of Perfection (2)").exists()
            )

    def test_same_recipient_and_title_cannot_save_a_second_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = ProjectStateController(root)
            state.initialize()
            first_identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            settings = SettingsStore(root)
            settings.update_fields({"recipient_title": "Morning Joy"})
            pages = root / "gallery" / "user" / "pages"
            pages.mkdir(parents=True)
            for name in REQUIRED_SLIDES:
                (pages / name).write_bytes(b"image")
            message = root / "gallery" / "user" / "message" / "message.html"
            message.parent.mkdir(parents=True)
            message.write_text("<p>Letter</p>", encoding="utf-8")
            resolver = ProjectPathResolver(root)
            service = ProjectSaveService(root, state, resolver=resolver)
            first_project = service.save_workspace_snapshot(
                reason="tab-switch"
            )

            state.begin_new_project()
            second_identity = state.establish_project(
                "Amanda Miller",
                custom_capitalization=True,
            )
            settings.update_fields({"recipient_title": "Morning Joy"})

            self.assertNotEqual(
                first_identity.project_id,
                second_identity.project_id,
            )
            with self.assertRaisesRegex(
                ProjectNotReadyError,
                "different letter title",
            ):
                service.save_workspace_snapshot(reason="tab-switch")
            self.assertTrue(first_project.is_dir())
            self.assertEqual(
                [path.name for path in first_project.parent.iterdir()],
                ["Morning Joy"],
            )

    def test_each_two_tab_combination_is_save_eligible(self) -> None:
        combinations = (
            ("images", "sound"),
            ("images", "message"),
            ("sound", "message"),
        )
        for completed in combinations:
            with self.subTest(completed=completed):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    state = ProjectStateController(root)
                    state.initialize()
                    state.establish_project(
                        "Amanda Miller",
                        custom_capitalization=True,
                    )
                    SettingsStore(root).update_fields(
                        {"recipient_title": "A Unique Title"}
                    )
                    if "images" in completed:
                        pages = root / "gallery" / "user" / "pages"
                        pages.mkdir(parents=True)
                        for name in REQUIRED_SLIDES:
                            (pages / name).write_bytes(b"image")
                    if "message" in completed:
                        message = (
                            root
                            / "gallery"
                            / "user"
                            / "message"
                            / "message.html"
                        )
                        message.parent.mkdir(parents=True)
                        message.write_text("<p>Letter</p>", encoding="utf-8")
                    if "sound" in completed:
                        source = root / "song.mp3"
                        source.write_bytes(b"sound")
                        record = import_runtime_track(
                            root,
                            source,
                            display_title="Song",
                        )
                        save_project_state(
                            root,
                            ProjectSoundState(
                                mode="single",
                                single_track_id=record.track_id,
                                selected_track_id=record.track_id,
                            ),
                        )

                    eligibility = evaluate_project_save_eligibility(root)

                    self.assertEqual(
                        eligibility.completed_tabs,
                        completed,
                    )
                    self.assertTrue(eligibility.can_save)

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
                editor.font_size_spin,
            ):
                self.assertTrue(control.isVisible())
                self.assertGreater(control.width(), 0)
            for control in (
                editor.btn_format,
                editor.btn_color,
                editor.btn_align,
                editor.btn_lists,
                editor.btn_spacing,
            ):
                self.assertTrue(editor.font_controls.isAncestorOf(control))
                self.assertFalse(editor.toolbar.isAncestorOf(control))
            toolbar_actions = editor.toolbar.actions()

            def widget_action(widget: QtWidgets.QWidget) -> QtGui.QAction:
                return next(
                    action
                    for action in toolbar_actions
                    if isinstance(action, QtWidgets.QWidgetAction)
                    and action.defaultWidget() is widget
                )

            def has_separator_between(
                left: QtGui.QAction,
                right: QtGui.QAction,
            ) -> bool:
                left_index = toolbar_actions.index(left)
                right_index = toolbar_actions.index(right)
                return any(
                    action.isSeparator()
                    for action in toolbar_actions[left_index + 1 : right_index]
                )

            link_action = widget_action(editor.btn_links)
            ultralink_action = widget_action(editor.btn_ultralink)
            ultralink_color_action = widget_action(editor.btn_ultralink_color)
            self.assertTrue(
                has_separator_between(editor.act_salutation, link_action)
            )
            self.assertTrue(has_separator_between(link_action, ultralink_action))
            self.assertTrue(
                has_separator_between(ultralink_color_action, editor.act_undo)
            )
            self.assertTrue(
                has_separator_between(editor.act_redo, editor.act_find)
            )
            self.assertFalse(hasattr(editor, "font_size_down"))
            self.assertFalse(hasattr(editor, "font_size_up"))
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

    def test_editor_font_size_picker_preserves_selection_and_boundaries(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.show()
            self.app.processEvents()
            editor.editor.setHtml("<p>Alpha Beta</p>")
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor.set_font_size(16)

            option = QtWidgets.QStyleOptionSpinBox()
            editor.font_size_spin.initStyleOption(option)
            up_button = editor.font_size_spin.style().subControlRect(
                QtWidgets.QStyle.CC_SpinBox,
                option,
                QtWidgets.QStyle.SC_SpinBoxUp,
                editor.font_size_spin,
            )
            down_button = editor.font_size_spin.style().subControlRect(
                QtWidgets.QStyle.CC_SpinBox,
                option,
                QtWidgets.QStyle.SC_SpinBoxDown,
                editor.font_size_spin,
            )
            self.assertGreaterEqual(up_button.width(), 22)
            self.assertGreaterEqual(up_button.height(), 14)
            self.assertGreaterEqual(down_button.width(), 22)
            self.assertGreaterEqual(down_button.height(), 14)

            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=up_button.center(),
            )
            selected = editor.editor.textCursor()
            self.assertEqual((selected.selectionStart(), selected.selectionEnd()), (0, 5))
            self.assertEqual(round(selected.charFormat().fontPointSize()), 17)

            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=up_button.center(),
            )
            self.assertEqual(
                round(editor.editor.textCursor().charFormat().fontPointSize()),
                18,
            )

            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=down_button.center(),
            )
            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=down_button.center(),
            )
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

            typing_cursor = editor.editor.textCursor()
            typing_cursor.clearSelection()
            typing_cursor.movePosition(QtGui.QTextCursor.End)
            editor.editor.setTextCursor(typing_cursor)
            editor.set_font_size(16)
            QtTest.QTest.mouseClick(
                editor.font_size_spin,
                QtCore.Qt.LeftButton,
                pos=up_button.center(),
            )
            typing_cursor = editor.editor.textCursor()
            typing_cursor.insertText("X")
            self.assertEqual(round(typing_cursor.charFormat().fontPointSize()), 17)

            editor.font_size_spin.setValue(100)
            editor.font_size_spin.stepUp()
            self.assertEqual(editor.font_size_spin.value(), 100)
            editor.font_size_spin.setValue(1)
            editor.font_size_spin.stepDown()
            self.assertEqual(editor.font_size_spin.value(), 1)
            self.assertFalse(editor.font_size_spin.keyboardTracking())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_salutation_restores_active_character_and_paragraph_format(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.recipient_name = "Amani Hill"
            editor.editor.setPlainText("Existing content")
            cursor = editor.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)

            block_format = cursor.blockFormat()
            block_format.setAlignment(QtCore.Qt.AlignRight)
            block_format.setLeftMargin(13.0)
            cursor.setBlockFormat(block_format)
            editor.editor.setTextCursor(cursor)

            char_format = QtGui.QTextCharFormat()
            char_format.setFontFamilies(["Onyx"])
            char_format.setFontPointSize(16.0)
            char_format.setFontWeight(QtGui.QFont.Bold)
            char_format.setFontItalic(True)
            char_format.setFontUnderline(True)
            char_format.setFontStrikeOut(True)
            char_format.setForeground(QtGui.QColor("#123456"))
            editor.editor.setCurrentCharFormat(char_format)

            editor.insert_salutation()

            self.assertEqual(
                editor.editor.toPlainText(),
                "Dear Amani Hill,\nExisting content",
            )
            salutation = QtGui.QTextCursor(editor.editor.document())
            salutation.setPosition(0)
            salutation.setPosition(len("Dear Amani Hill,"), QtGui.QTextCursor.KeepAnchor)
            salutation_format = salutation.charFormat()
            self.assertEqual(salutation_format.fontFamilies()[0], "Papyrus")
            self.assertEqual(round(salutation_format.fontPointSize()), 50)

            restored_cursor = editor.editor.textCursor()
            self.assertEqual(restored_cursor.positionInBlock(), 0)
            self.assertEqual(restored_cursor.block().text(), "Existing content")
            self.assertEqual(
                restored_cursor.blockFormat().alignment(),
                QtCore.Qt.AlignRight,
            )
            self.assertEqual(restored_cursor.blockFormat().leftMargin(), 13.0)

            restored_format = editor.editor.currentCharFormat()
            self.assertEqual(restored_format.fontFamilies()[0], "Onyx")
            self.assertEqual(round(restored_format.fontPointSize()), 16)
            self.assertEqual(restored_format.fontWeight(), QtGui.QFont.Bold)
            self.assertTrue(restored_format.fontItalic())
            self.assertTrue(restored_format.fontUnderline())
            self.assertTrue(restored_format.fontStrikeOut())
            self.assertEqual(restored_format.foreground().color().name(), "#123456")

            restored_cursor.insertText("X")
            typed = QtGui.QTextCursor(editor.editor.document())
            typed.setPosition(len("Dear Amani Hill,") + 1)
            typed.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
            typed_format = typed.charFormat()
            self.assertEqual(typed.selectedText(), "X")
            self.assertEqual(typed_format.fontFamilies()[0], "Onyx")
            self.assertEqual(round(typed_format.fontPointSize()), 16)
            self.assertEqual(typed_format.foreground().color().name(), "#123456")
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

    def test_text_color_button_tracks_uniform_and_mixed_selection_colors(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.show()
            self.app.processEvents()
            editor.editor.setHtml(
                '<p><span style="color:#ff0000">Alpha</span> '
                '<span style="color:#0000ff">Beta</span></p>'
            )
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(5, QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor._sync_current_format()

            self.assertFalse(editor._text_color_indicator_mixed)
            self.assertEqual(editor._active_text_color.name(), "#ff0000")
            self.assertEqual(editor.btn_color.toolTip(), "Text color: #ff0000")
            self.assertGreaterEqual(editor.btn_color.iconSize().height(), 28)
            style_option = QtWidgets.QStyleOptionToolButton()
            editor.btn_color.initStyleOption(style_option)
            self.assertGreaterEqual(style_option.iconSize.height(), 28)

            editor.settings = mock.Mock()
            with mock.patch(
                "Editor.QColorDialog.getColor",
                return_value=QtGui.QColor("#00ff00"),
            ) as picker:
                editor.btn_color.click()
            self.assertEqual(picker.call_args.args[0].name(), "#ff0000")

            cursor = editor.editor.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            editor.editor.setTextCursor(cursor)
            editor._sync_current_format()

            self.assertTrue(editor._text_color_indicator_mixed)
            self.assertEqual(
                editor._text_color_indicator_colors,
                (
                    "#ff0000",
                    "#ff7f00",
                    "#ffd700",
                    "#00a651",
                    "#0066ff",
                    "#8f00ff",
                ),
            )
            self.assertIn("Mixed text colors", editor.btn_color.toolTip())
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_default_ultralink_color_button_is_direct_and_always_available(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            cursor = editor.editor.textCursor()
            cursor.movePosition(QtGui.QTextCursor.End)
            editor.editor.setTextCursor(cursor)
            editor._update_ultralink_action()

            self.assertFalse(editor.btn_ultralink.isEnabled())
            self.assertTrue(editor.btn_ultralink_color.isEnabled())
            self.assertEqual(editor.btn_ultralink_color.text(), "")
            self.assertIsNone(editor.btn_ultralink.menu())
            self.assertIsNone(editor.btn_ultralink_color.menu())

            editor.settings = mock.Mock()
            with mock.patch(
                "Editor.QColorDialog.getColor",
                return_value=QtGui.QColor("#00ffff"),
            ) as picker:
                editor.btn_ultralink_color.click()

            self.assertEqual(picker.call_args.args[2], "Default Ultra Link Color Picker")
            self.assertEqual(editor.ultralink_color.name(), "#00ffff")
            self.assertIn("color:#00ffff", editor.btn_ultralink.styleSheet())
            self.assertIn("background:#00ffff", editor.btn_ultralink_color.styleSheet())
            editor.settings.setValue.assert_called_once()
            editor.settings.sync.assert_called_once()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_popup_is_minimal_and_selection_aware(self) -> None:
        single = UltralinkDialog(
            selection_text="  Amani  ",
        )
        multiple = UltralinkDialog(selection_text="Amani Hill")
        try:
            self.assertTrue(single.windowFlags() & QtCore.Qt.Popup)
            self.assertTrue(single.windowFlags() & QtCore.Qt.FramelessWindowHint)
            self.assertEqual(
                single.instruction_label.text(),
                "Ultra Link: Whatever you type here is shown when the user "
                "hovers over this word.",
            )
            self.assertEqual(
                multiple.instruction_label.text(),
                "Ultra Link: Whatever you type here is shown when the user "
                "hovers over these words.",
            )
            self.assertEqual(
                [button.text() for button in single.findChildren(QtWidgets.QPushButton)],
                ["Save"],
            )
            self.assertFalse(single.findChildren(QtWidgets.QDialogButtonBox))
            self.assertFalse(single.findChildren(QtWidgets.QCheckBox))
            self.assertFalse(single.findChildren(QtWidgets.QComboBox))
        finally:
            single.deleteLater()
            multiple.deleteLater()
            self.app.processEvents()

    def test_ultralink_popup_click_outside_rejects(self) -> None:
        editor, holder = self._make_editor("paper")
        dialog = UltralinkDialog(
            "Original hover text",
            selection_text="Amani",
            parent=editor,
        )
        try:
            editor.show()
            self.app.processEvents()
            dialog.move(editor.mapToGlobal(QtCore.QPoint(editor.width() + 20, 0)))
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            dialog.message_edit.setPlainText("Unsaved change")
            QtTest.QTest.mouseClick(
                dialog.message_edit.viewport(),
                QtCore.Qt.LeftButton,
                pos=dialog.message_edit.viewport().rect().center(),
            )
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())

            QtTest.QTest.mouseClick(
                editor,
                QtCore.Qt.LeftButton,
                pos=editor.rect().center(),
            )
            self.app.processEvents()

            self.assertFalse(dialog.isVisible())
            self.assertEqual(dialog.result(), QtWidgets.QDialog.Rejected)
        finally:
            dialog.reject()
            dialog.deleteLater()
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_save_applies_exact_phrase_to_every_occurrence(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            content = "Amani Hill met Amani Hill. Amani waited."
            editor.editor.setPlainText(content)
            cursor = editor.editor.textCursor()
            cursor.setPosition(0)
            cursor.setPosition(len("Amani Hill"), QtGui.QTextCursor.KeepAnchor)
            editor.editor.setTextCursor(cursor)
            editor.settings = mock.Mock()
            editor.ultralink_color = QtGui.QColor("#00ffff")

            popup = mock.Mock()
            popup.exec.return_value = QtWidgets.QDialog.Accepted
            popup.message_text.return_value = "Amani's profile"
            with mock.patch("Editor.UltralinkDialog", return_value=popup), mock.patch.object(
                QtWidgets.QToolTip,
                "showText",
            ):
                editor.open_ultralink_dialog()

            first = content.find("Amani Hill")
            second = content.find("Amani Hill", first + 1)
            for position in (first, second):
                probe = QtGui.QTextCursor(editor.editor.document())
                probe.setPosition(position)
                probe.movePosition(
                    QtGui.QTextCursor.NextCharacter,
                    QtGui.QTextCursor.KeepAnchor,
                )
                char_format = probe.charFormat()
                self.assertTrue(char_format.isAnchor())
                self.assertEqual(
                    ultralink_message_from_href(char_format.anchorHref()),
                    "Amani's profile",
                )
                self.assertEqual(char_format.foreground().color().name(), "#00ffff")

            lone_name = content.rfind("Amani")
            probe = QtGui.QTextCursor(editor.editor.document())
            probe.setPosition(lone_name)
            probe.movePosition(
                QtGui.QTextCursor.NextCharacter,
                QtGui.QTextCursor.KeepAnchor,
            )
            self.assertFalse(probe.charFormat().isAnchor())
            self.assertEqual(editor.ultralink_color.name(), "#00ffff")
            self.assertIn("#00ffff", editor.editor.toHtml().lower())
            editor.settings.setValue.assert_not_called()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_cancel_discards_document_and_color_changes(self) -> None:
        editor, holder = self._make_editor("paper")
        try:
            editor.editor.setPlainText("Amani")
            cursor = editor.editor.textCursor()
            cursor.select(QtGui.QTextCursor.Document)
            editor.editor.setTextCursor(cursor)
            editor.settings = mock.Mock()
            editor.ultralink_color = QtGui.QColor("#ffd84d")
            original_html = editor.editor.toHtml()

            popup = mock.Mock()
            popup.exec.return_value = QtWidgets.QDialog.Rejected
            popup.message_text.return_value = "Unsaved hover text"
            with mock.patch("Editor.UltralinkDialog", return_value=popup):
                editor.open_ultralink_dialog()

            self.assertEqual(editor.editor.toHtml(), original_html)
            self.assertEqual(editor.ultralink_color.name(), "#ffd84d")
            editor.settings.setValue.assert_not_called()
        finally:
            editor._closing = True
            editor.close()
            editor.deleteLater()
            editor._host_for_test.deleteLater()
            self.app.processEvents()
            holder.cleanup()

    def test_ultralink_default_color_persists_across_editors(self) -> None:
        class MemorySettings:
            values: dict[str, object] = {}

            def __init__(self, *_args, **_kwargs) -> None:
                self.synced = False

            def value(self, key: str, default=None):
                return self.values.get(key, default)

            def setValue(self, key: str, value: object) -> None:
                self.values[key] = value

            def sync(self) -> None:
                self.synced = True

        first = second = None
        first_holder = second_holder = None
        with mock.patch("Editor.QSettings", MemorySettings):
            try:
                first, first_holder = self._make_editor("paper")
                first._persist_ultralink_color(QtGui.QColor("#00ffff"))
                self.assertTrue(first.settings.synced)

                second, second_holder = self._make_editor("paper")
                self.assertEqual(second.ultralink_color.name(), "#00ffff")
            finally:
                for editor, holder in (
                    (first, first_holder),
                    (second, second_holder),
                ):
                    if editor is not None:
                        editor._closing = True
                        editor.close()
                        editor.deleteLater()
                        editor._host_for_test.deleteLater()
                    if holder is not None:
                        holder.cleanup()
                self.app.processEvents()

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
