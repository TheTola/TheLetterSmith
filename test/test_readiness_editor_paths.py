from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from Editor import Editor
from Forge_Tab import ReadinessWindow
from project_paths import ProjectPathError, ProjectPathResolver
from project_store import ProjectStore
from readiness import evaluate_readiness
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
