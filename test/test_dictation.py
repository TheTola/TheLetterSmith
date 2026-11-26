from __future__ import annotations

import os
from array import array
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from dictation import DictationManager, _configure, attach_dictation
from dictation_speech import NativeRecognizer, PCMConverter, SpeechService, SpeechWorker


class _Speech:
    def __init__(self):
        self.callbacks = []
        self.canceled = []
        self.stopped = []

    def start(self, callback):
        self.callbacks.append(callback)
        return f"session-{len(self.callbacks)}"

    def cancel(self, identity=None):
        self.canceled.append(identity)

    def stop(self, identity):
        self.stopped.append(identity)


class DictationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout(self.window)
        self.speech = _Speech()
        self.manager = mock.Mock()
        self.manager.active = None
        self.manager.speech = self.speech
        self.manager.ensure_model.return_value = True
        self.manager_patch = mock.patch("dictation._manager", return_value=self.manager)
        self.manager_patch.start()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        for editor in self.window.findChildren(QtWidgets.QWidget):
            binding = getattr(editor, "_dictation_binding", None)
            if binding is not None:
                binding.cancel()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.manager_patch.stop()

    def make_editor(self, kind, text="Existing"):
        editor = kind(self.window)
        editor.setPlainText(text)
        editor.moveCursor(QtGui.QTextCursor.End)
        self.layout.addWidget(editor)
        binding = attach_dictation(editor)
        editor.show()
        self.app.processEvents()
        return editor, binding

    def test_partial_stays_out_of_document_and_final_commits_with_undo(self):
        for kind in (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit):
            with self.subTest(editor=kind.__name__):
                editor, binding = self.make_editor(kind)
                binding.toggle()
                callback = self.speech.callbacks[-1]
                callback({"event": "partial", "text": "unfinished"})
                self.assertEqual(editor.toPlainText(), "Existing")
                self.assertTrue(binding.active)
                callback({"event": "final", "text": "spoken words"})
                self.assertEqual(editor.toPlainText(), "Existing spoken words")
                callback({"event": "done"})
                editor.undo()
                self.assertEqual(editor.toPlainText(), "Existing")

    def test_finished_signal_follows_final_text_and_inactive_state(self):
        editor, binding = self.make_editor(QtWidgets.QPlainTextEdit)
        observed = []
        binding.finished.connect(lambda: observed.append((binding.active, binding.finishing, editor.toPlainText())))
        binding.toggle()
        callback = self.speech.callbacks[-1]
        callback({"event": "final", "text": "complete phrase"})
        self.assertEqual(observed, [])
        callback({"event": "done"})
        self.assertEqual(observed, [(False, False, "Existing complete phrase")])

    def test_selection_is_preserved_and_speech_is_inserted_after_it(self):
        for kind in (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit):
            with self.subTest(editor=kind.__name__):
                editor, binding = self.make_editor(kind, "Original suffix")
                cursor = editor.textCursor()
                cursor.setPosition(0)
                cursor.setPosition(8, QtGui.QTextCursor.KeepAnchor)
                editor.setTextCursor(cursor)
                binding.toggle()
                self.speech.callbacks[-1]({"event": "final", "text": "dictated"})
                self.assertEqual(editor.toPlainText(), "Original dictated suffix")
                binding.cancel()

    def test_rich_text_final_preserves_current_format_and_undo(self):
        editor, binding = self.make_editor(QtWidgets.QTextEdit)
        formatting = QtGui.QTextCharFormat()
        formatting.setFontWeight(QtGui.QFont.Bold)
        formatting.setForeground(QtGui.QColor("#a03040"))
        editor.mergeCurrentCharFormat(formatting)
        binding.toggle()
        self.speech.callbacks[-1]({"event": "final", "text": "formatted"})
        probe = editor.textCursor()
        probe.movePosition(QtGui.QTextCursor.PreviousCharacter, QtGui.QTextCursor.KeepAnchor)
        self.assertEqual(probe.charFormat().fontWeight(), QtGui.QFont.Bold)
        self.assertEqual(probe.charFormat().foreground().color().name(), "#a03040")
        binding.cancel()
        editor.undo()
        self.assertEqual(editor.toPlainText(), "Existing")

    def test_cancel_discards_preedit_and_ignores_results_from_prior_session(self):
        editor, binding = self.make_editor(QtWidgets.QPlainTextEdit)
        binding.toggle()
        old_callback = self.speech.callbacks[-1]
        old_callback({"event": "partial", "text": "unfinished"})
        binding.cancel()
        binding.toggle()
        old_callback({"event": "final", "text": "wrong destination"})
        old_callback({"event": "done"})
        self.assertTrue(binding.active)
        self.assertEqual(editor.toPlainText(), "Existing")
        self.speech.callbacks[-1]({"event": "final", "text": "current"})
        self.assertEqual(editor.toPlainText(), "Existing current")

    def test_blocked_external_replacement_cannot_receive_late_speech(self):
        for kind in (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit):
            with self.subTest(editor=kind.__name__):
                editor, binding = self.make_editor(kind)
                binding.toggle()
                callback = self.speech.callbacks[-1]
                with QtCore.QSignalBlocker(editor):
                    editor.setPlainText("Other destination")
                callback({"event": "final", "text": "must not appear"})
                self.assertEqual(editor.toPlainText(), "Other destination")
                self.assertFalse(binding.active)

    def test_typing_caret_change_and_hiding_cancel_recording(self):
        for action in ("typing", "caret", "hide"):
            with self.subTest(action=action):
                editor, binding = self.make_editor(QtWidgets.QPlainTextEdit)
                binding.toggle()
                callback = self.speech.callbacks[-1]
                if action == "typing":
                    QtTest.QTest.keyClicks(editor, "x")
                elif action == "caret":
                    editor.moveCursor(QtGui.QTextCursor.Start)
                else:
                    editor.hide()
                self.assertFalse(binding.active)
                current = editor.toPlainText()
                callback({"event": "final", "text": "stale"})
                self.assertEqual(editor.toPlainText(), current)

    def test_starting_another_field_cancels_previous_session(self):
        first, first_binding = self.make_editor(QtWidgets.QPlainTextEdit, "First")
        second, second_binding = self.make_editor(QtWidgets.QPlainTextEdit, "Second")
        first_binding.toggle()
        first_callback = self.speech.callbacks[-1]
        second_binding.toggle()
        self.assertFalse(first_binding.active)
        self.assertTrue(second_binding.active)
        self.assertIs(self.manager.active, second_binding)
        first_callback({"event": "final", "text": "stale"})
        self.speech.callbacks[-1]({"event": "final", "text": "current"})
        self.assertEqual(first.toPlainText(), "First")
        self.assertEqual(second.toPlainText(), "Second current")

    def test_stop_waits_for_final_and_done(self):
        editor, binding = self.make_editor(QtWidgets.QPlainTextEdit)
        binding.toggle()
        callback = self.speech.callbacks[-1]
        binding.toggle()
        self.assertTrue(binding.active)
        self.assertTrue(binding.finishing)
        self.assertEqual(self.speech.stopped, ["session-1"])
        callback({"event": "final", "text": "last phrase"})
        callback({"event": "done"})
        self.assertFalse(binding.active)
        self.assertIsNone(self.manager.active)
        self.assertEqual(editor.toPlainText(), "Existing last phrase")

    def test_stop_button_click_finishes_instead_of_restarting(self):
        editor, binding = self.make_editor(QtWidgets.QPlainTextEdit)
        QtTest.QTest.mouseClick(binding.button, QtCore.Qt.LeftButton)
        self.assertTrue(binding.active)
        QtTest.QTest.mouseClick(binding.button, QtCore.Qt.LeftButton)
        self.assertTrue(binding.active)
        self.assertTrue(binding.finishing)
        self.assertEqual(len(self.speech.callbacks), 1)
        self.assertEqual(self.speech.stopped, ["session-1"])

    def test_signal_blocked_document_replacement_cannot_receive_old_results(self):
        editor, binding = self.make_editor(QtWidgets.QTextEdit)
        binding.toggle()
        callback = self.speech.callbacks[-1]
        document = QtGui.QTextDocument(editor)
        document.setPlainText("New document")
        with QtCore.QSignalBlocker(editor):
            editor.setDocument(document)
        callback({"event": "final", "text": "stale"})
        self.assertEqual(editor.toPlainText(), "New document")
        self.assertFalse(binding.active)

    def test_read_only_change_cancels_and_prevents_start(self):
        editor, binding = self.make_editor(QtWidgets.QPlainTextEdit)
        binding.toggle()
        callback = self.speech.callbacks[-1]
        editor.setReadOnly(True)
        self.assertFalse(binding.active)
        callback({"event": "final", "text": "stale"})
        binding.toggle()
        self.assertEqual(len(self.speech.callbacks), 1)
        self.assertEqual(editor.toPlainText(), "Existing")

    def test_microphone_gutter_follows_scrollbar_without_canceling_final_insert(self):
        for kind in (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit):
            with self.subTest(editor=kind.__name__):
                editor, binding = self.make_editor(kind)
                editor.setFixedSize(320, 110)
                self.app.processEvents()
                scrollbar = editor.verticalScrollBar()
                self.assertFalse(scrollbar.isVisible())
                initial_x = binding.button.x()
                self.assertFalse(binding.button.geometry().intersects(editor.viewport().geometry()))

                binding.toggle()
                callback = self.speech.callbacks[-1]
                callback({"event": "final", "text": "A dictated line.\n" * 100})
                self.app.processEvents()
                self.assertTrue(binding.active)
                self.assertTrue(scrollbar.isVisible())
                scrollbar_rect = QtCore.QRect(scrollbar.mapTo(editor, QtCore.QPoint()), scrollbar.size())
                self.assertFalse(binding.button.geometry().intersects(scrollbar_rect))
                self.assertFalse(binding.button.geometry().intersects(editor.viewport().geometry()))
                self.assertLess(binding.button.x(), initial_x)

                callback({"event": "done"})
                editor.clear()
                self.app.processEvents()
                self.assertFalse(scrollbar.isVisible())
                self.assertEqual(binding.button.x(), initial_x)
                self.assertFalse(binding.button.geometry().intersects(editor.viewport().geometry()))

    def test_editor_context_menu_is_preserved_and_mic_exposes_settings(self):
        class CustomEditor(QtWidgets.QTextEdit):
            context_menus = 0

            def contextMenuEvent(self, event):
                self.context_menus += 1
                event.accept()

        editor, binding = self.make_editor(CustomEditor)
        menus = []
        menu_type = QtWidgets.QMenu

        def make_menu(parent):
            menu = menu_type(parent)
            menu.exec = mock.Mock(side_effect=lambda *_args: menus.append(
                [action.text() for action in menu.actions()]
            ))
            return menu

        point = QtCore.QPoint(5, 5)
        with mock.patch("dictation.QtWidgets.QMenu", side_effect=make_menu):
            viewport_event = QtGui.QContextMenuEvent(
                QtGui.QContextMenuEvent.Mouse, point, editor.viewport().mapToGlobal(point)
            )
            self.app.sendEvent(editor.viewport(), viewport_event)
            self.assertEqual(editor.context_menus, 1)
            self.assertEqual(menus, [])

            microphone_event = QtGui.QContextMenuEvent(
                QtGui.QContextMenuEvent.Mouse, point, binding.button.mapToGlobal(point)
            )
            self.app.sendEvent(binding.button, microphone_event)
        self.assertEqual(editor.context_menus, 1)
        self.assertEqual(menus, [["Dictation settings…"]])


class DictationBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_fragmented_stereo_capture_preserves_resampling_continuity(self):
        samples = array("h")
        for index in range(1200):
            samples.extend((-24000 + index * 37 % 48000, 8000 - index * 11 % 16000))
        raw = samples.tobytes()
        whole = PCMConverter(48000, 2, "int16").convert(raw)
        converter = PCMConverter(48000, 2, "int16")
        fragmented = array("f")
        for offset in range(0, len(raw), 37):
            fragmented.extend(converter.convert(raw[offset:offset + 37]))
        self.assertEqual(fragmented, whole)
        self.assertEqual(len(fragmented), 400)
        self.assertAlmostEqual(fragmented[0], (-24000 + 8000) / 2 / 32768)

    def test_eight_second_backlog_stops_before_recognizing_queued_audio(self):
        worker = SpeechWorker("session", "unused", (16000, 1, "float"))
        events = []
        worker.event.connect(lambda identity, event: events.append(event))
        runtime = mock.Mock()
        worker.submit(bytes(8 * 16000 * 4))
        worker.submit(bytes(4))
        with mock.patch("dictation_speech.NativeRecognizer", return_value=runtime):
            worker.run()
        runtime.recognize.assert_not_called()
        runtime.close.assert_called_once()
        errors = [event for event in events if event["event"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("eight seconds behind", errors[0]["message"])
        self.assertEqual(events[-1]["event"], "done")

    def test_missing_or_corrupted_selected_pack_never_launches_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            corrupt = root / "corrupt"
            corrupt.mkdir()
            (corrupt / "ggml-small.en-q5_1.bin").write_bytes(b"invalid weights")
            (corrupt / "ggml-silero-v6.2.0.bin").write_bytes(b"invalid detector")
            for pack in (root / "missing", corrupt):
                with self.subTest(pack=pack.name), mock.patch("dictation_speech.subprocess.Popen") as launch:
                    with self.assertRaises((OSError, ValueError)):
                        NativeRecognizer(pack, threading.Event())
                    launch.assert_not_called()

    def test_unavailable_session_monitor_prevents_capture_and_worker_start(self):
        store = mock.Mock()
        store.load.return_value = {"dictation_model_pack": "verified-pack"}
        monitor = mock.Mock(available=False, locked=False)
        with (mock.patch("dictation.DictationSettings", return_value=store),
              mock.patch("dictation_session.DictationSessionMonitor", return_value=monitor)):
            manager = DictationManager(self.app)
        events = []
        try:
            with (mock.patch("dictation_speech.QMediaDevices.audioInputs") as devices,
                  mock.patch("dictation_speech.QAudioSource") as capture,
                  mock.patch("dictation_speech.SpeechWorker") as worker):
                result = manager.speech.start(events.append)
            self.assertIsNone(result)
            devices.assert_not_called()
            capture.assert_not_called()
            worker.assert_not_called()
            self.assertEqual([event["event"] for event in events], ["error", "done"])
            self.assertIn("session monitoring is unavailable", events[0]["message"])
        finally:
            self.app.aboutToQuit.disconnect(manager.dispose)
            manager.dispose()
            manager.deleteLater()

    def test_malformed_native_results_are_rejected_before_delivery(self):
        recognizer = NativeRecognizer.__new__(NativeRecognizer)
        recognizer.canceled = threading.Event()
        recognizer.responses = queue.Queue()
        for response in (None, {}, {"event": "result", "speech": True, "text": 123},
                         {"event": "result", "speech": "yes", "text": "words"},
                         {"event": "result", "speech": True, "text": "x" * 32001},
                         {"event": "result", "speech": True, "text": "words", "base_tiebreaks": -1}):
            with self.subTest(response=str(response)[:70]):
                recognizer.responses.put(response)
                with self.assertRaises(ValueError):
                    recognizer.receive(.1)
        with self.assertRaises(TimeoutError):
            recognizer.receive(0)

    def test_late_worker_events_cannot_reach_a_new_destination(self):
        service = SpeechService(mock.Mock())
        callback = mock.Mock()
        service.identity = "new"
        service.callback = callback
        for event in ({}, {"event": "ready"}, {"event": "final", "text": "stale"}, {"event": "done"}):
            service._event("old", event)
        callback.assert_not_called()
        self.assertEqual(service.identity, "new")
        current = {"event": "final", "text": "current"}
        service._event("new", current)
        callback.assert_called_once_with(current)
        service.cancel()

    def test_failed_setup_keeps_existing_model_selection(self):
        class FailedDownload(QtCore.QObject):
            progress = QtCore.Signal(int, int)
            completed = QtCore.Signal(str)
            failed = QtCore.Signal(str)
            finished = QtCore.Signal()

            def start(self):
                self.failed.emit("Model verification failed")
                self.finished.emit()

        store = mock.Mock()
        selected = {"dictation_model_pack": "existing-verified-pack", "dictation_microphone": ""}
        store.load.side_effect = lambda: dict(selected)
        manager = mock.Mock(store=store)

        def exercise(dialog):
            dialog.start_download()
            self.assertIn("verification failed", dialog.status.text())
            self.assertEqual(store.load()["dictation_model_pack"], "existing-verified-pack")
            self.assertIsNone(dialog.worker)
            return QtWidgets.QDialog.Rejected

        with (mock.patch("dictation_models.ModelDownloadWorker", FailedDownload),
              mock.patch("PySide6.QtMultimedia.QMediaDevices.audioInputs", return_value=[]),
              mock.patch("ui_dialogs.LetterSmithDialog.exec", exercise)):
            _configure(manager, None)
        store.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
