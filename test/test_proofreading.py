import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from shiboken6 import delete

from language_service import LanguageIssue, LanguageService
from proofreading import (
    PROOFREADING_SELECTION_PROPERTY,
    attach_proofreading,
)


class _Grammar:
    def __init__(self):
        self.requests = []
        self.cancelled = []

    def ready(self):
        return True

    def submit(self, owner, text, *, context, protected_terms, callback):
        self.requests.append((text, callback, protected_terms))
        return len(self.requests)

    def cancel(self, owner):
        self.cancelled.append(owner)


class _Dictation(QtCore.QObject):
    finished = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.active = True
        self.finishing = False
        self.embedded = False


class ProofreadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = LanguageService(Path(self.directory.name))
        self.grammar = _Grammar()
        self.module = types.SimpleNamespace(get_grammar_service=lambda: self.grammar,
                                            open_setup=mock.Mock())
        self.patch = mock.patch.dict(sys.modules, {"language_ai": self.module})
        self.patch.start()
        self.editors = []

    def tearDown(self):
        for editor in self.editors:
            editor._proofreading_binding.cancel()
            delete(editor)
        self.app.processEvents()
        self.patch.stop()
        self.directory.cleanup()

    def make_editor(self, kind=QtWidgets.QPlainTextEdit, **kwargs):
        editor = kind()
        editor.resize(500, 240)
        editor.show()
        editor.setFocus()
        self.app.processEvents()
        binding = attach_proofreading(editor, service=self.service, **kwargs)
        self.editors.append(editor)
        return editor, binding

    def check(self, binding):
        binding._dictionary_timer.stop()
        binding._ai_timer.stop()
        binding._check_dictionary()

    def test_user_completed_typos_correct_but_grammar_and_current_word_do_not(self):
        editor, binding = self.make_editor()
        QtTest.QTest.keyClicks(editor, "teh ")
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "the ")
        QtTest.QTest.keyClicks(editor, "teh")
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "the teh")
        self.assertTrue(any(issue.text == "teh" for issue in binding.issues))
        editor.setPlainText("")
        QtTest.QTest.keyClicks(editor, "a apple ")
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "a apple ")

    def test_loads_and_programmatic_changes_never_autocorrect_or_request_ai(self):
        editor, binding = self.make_editor()
        editor.setPlainText("teh old text ")
        self.check(binding)
        binding._check_ai()
        self.assertEqual(editor.toPlainText(), "teh old text ")
        self.assertFalse(self.grammar.requests)
        self.assertTrue(any(issue.text == "teh" for issue in binding.issues))
        with binding.suspend():
            QtTest.QTest.keyClicks(editor, "teh ")
        self.check(binding)
        self.assertTrue(editor.toPlainText().startswith("teh "))

    def test_undo_does_not_immediately_reapply_autocorrection(self):
        editor, binding = self.make_editor()
        QtTest.QTest.keyClicks(editor, "teh ")
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "the ")
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key_Z, QtCore.Qt.ControlModifier)
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "teh ")

    def test_typing_elsewhere_preserves_historical_typos(self):
        editor, binding = self.make_editor()
        editor.setPlainText("teh historical word.\n")
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, "teh ")
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "teh historical word.\nthe ")
        editor.setPlainText("teh")
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, " ")
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "the ")

    def test_dictation_defers_corrections_and_finishes_last_word(self):
        editor, binding = self.make_editor()
        dictation = _Dictation()
        editor._dictation_binding = dictation
        binding._connect_dictation()
        event = QtGui.QInputMethodEvent("", [])
        event.setCommitString("teh")
        QtWidgets.QApplication.sendEvent(editor, event)
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "teh")
        self.assertTrue(dictation.active)
        dictation.active = False
        dictation.finished.emit()
        self.check(binding)
        self.assertEqual(editor.toPlainText(), "the")

    def test_real_dictation_binding_drains_before_correcting_final_word(self):
        from dictation import attach_dictation
        for kind in (QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit):
            with self.subTest(editor=kind.__name__):
                editor, binding = self.make_editor(kind)
                callbacks = []
                speech = mock.Mock()
                speech.start.side_effect = lambda callback: callbacks.append(callback) or "speech-session"
                manager = mock.Mock(active=None, speech=speech)
                manager.ensure_model.return_value = True
                with mock.patch("dictation._manager", return_value=manager):
                    microphone = attach_dictation(editor)
                    binding._connect_dictation()
                    microphone.toggle()
                    callbacks[-1]({"event": "partial", "text": "beuti"})
                    self.check(binding)
                    self.assertEqual(editor.toPlainText(), "")
                    callbacks[-1]({"event": "final", "text": "beutiful"})
                    self.check(binding)
                    self.assertEqual(editor.toPlainText(), "beutiful")
                    microphone.toggle()
                    self.assertTrue(microphone.finishing)
                    self.check(binding)
                    self.assertEqual(editor.toPlainText(), "beutiful")
                    callbacks[-1]({"event": "done"})
                    self.check(binding)
                    self.assertEqual(editor.toPlainText(), "beautiful")
                    speech.cancel.assert_not_called()

    def test_ai_is_suggestions_only_and_stale_identity_is_rejected(self):
        identity = ["page-one"]
        editor, binding = self.make_editor(identity=lambda: identity[0])
        QtTest.QTest.keyClicks(editor, "She go home.")
        binding._check_ai()
        text, callback, _terms = self.grammar.requests[-1]
        self.assertEqual(text, "She go home.")
        callback(1, "She goes home.", "")
        self.app.processEvents()
        self.assertEqual(editor.toPlainText(), "She go home.")
        issue = next(issue for issue in binding.issues if issue.category == "grammar" and issue.replacement == "goes")
        identity[0] = "page-two"
        self.assertFalse(binding.replace_issue(issue, "goes"))
        self.assertEqual(editor.toPlainText(), "She go home.")

    def test_stale_ai_results_do_not_mark_new_text(self):
        editor, binding = self.make_editor()
        QtTest.QTest.keyClicks(editor, "She go home.")
        binding._check_ai()
        callback = self.grammar.requests[-1][1]
        editor.setPlainText("Different document.")
        callback(1, "She goes home.", "")
        self.app.processEvents()
        self.assertFalse(binding._ai_issues)
        self.assertEqual(editor.toPlainText(), "Different document.")

    def test_paragraph_requests_exclude_separators_and_keep_document_offsets(self):
        editor, binding = self.make_editor()
        source = "She go home.\n\nThe cats is happy.\n"
        editor.setPlainText(source)
        binding._check_ai(force=True)
        text, callback, _terms = self.grammar.requests[-1]
        self.assertEqual(text, "She go home.")
        callback(1, "She goes home.", "")
        self.app.processEvents()
        text, callback, _terms = self.grammar.requests[-1]
        self.assertEqual(text, "The cats is happy.")
        callback(2, "The cats are happy.", "")
        self.app.processEvents()
        self.assertEqual(editor.toPlainText(), source)
        issue = next(item for item in binding._ai_issues if item.replacement == "are")
        self.assertEqual(issue.start, source.index("is"))
        self.assertTrue(binding.replace_issue(issue, "are"))
        self.assertEqual(editor.toPlainText(), "She go home.\n\nThe cats are happy.\n")

    def test_ai_chunks_changed_paragraphs_and_rejects_custom_word_rewrite(self):
        editor, binding = self.make_editor()
        self.service.add_to_dictionary("Karaethon")
        editor.setPlainText("Historical line.\n")
        editor.moveCursor(QtGui.QTextCursor.End)
        QtTest.QTest.keyClicks(editor, "Karaethon go home.")
        binding._check_ai()
        text, callback, terms = self.grammar.requests[-1]
        self.assertEqual(text, "Karaethon go home.")
        self.assertIn("Karaethon", terms)
        callback(1, "Karen goes home.", "")
        self.app.processEvents()
        self.assertFalse(any("Karaethon" in issue.text for issue in binding._ai_issues))
        self.assertTrue(all(len(chunk) <= 6000 for _start, chunk in binding._changed_chunks("word " * 2000, force=True)))

    def test_rich_text_offsets_format_undo_and_semantic_overlays_survive(self):
        editor, binding = self.make_editor(QtWidgets.QTextEdit)
        editor.setHtml('<p>😀 <b style="color:#ff0000">teh</b> note</p>')
        external = QtWidgets.QTextEdit.ExtraSelection()
        external.cursor = QtGui.QTextCursor(editor.document())
        external.cursor.setPosition(0)
        external.cursor.movePosition(QtGui.QTextCursor.NextCharacter, QtGui.QTextCursor.KeepAnchor)
        external.format.setForeground(QtGui.QColor("blue"))
        editor.setExtraSelections([external])
        self.check(binding)
        issue = next(issue for issue in binding.issues if issue.text == "teh")
        self.assertEqual(len(editor.extraSelections()), len(binding.issues) + 1)
        self.assertTrue(any(item.format.property(PROOFREADING_SELECTION_PROPERTY) for item in editor.extraSelections()))
        self.assertTrue(binding.replace_issue(issue, "the"))
        self.assertEqual(editor.toPlainText(), "😀 the note")
        cursor = QtGui.QTextCursor(editor.document())
        cursor.setPosition(3)
        cursor.setPosition(6, QtGui.QTextCursor.KeepAnchor)
        self.assertTrue(cursor.charFormat().font().bold())
        self.assertEqual(cursor.charFormat().foreground().color().name(), "#ff0000")
        editor.undo()
        self.assertEqual(editor.toPlainText(), "😀 teh note")


class GrammarProtectionTests(unittest.TestCase):
    def test_grammar_change_does_not_match_protected_word_substrings(self):
        from language_ai import validate_result
        self.assertEqual(validate_result("She said go.", "She says go.", ("ai",)), "She says go.")

    def test_custom_words_preserve_exact_case(self):
        from language_ai import validate_result
        with self.assertRaises(ValueError):
            validate_result("zorblin goes home.", "Zorblin goes home.", ("Zorblin",))

    def test_quoted_text_cannot_be_rewritten(self):
        from language_ai import validate_result
        for source, replacement in (('Keep "teh" exact.', 'Keep "the" exact.'),
                                    ("Keep 'zorblin' exact.", "Keep 'Zorblin' exact.")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_result(source, replacement)

    def test_technical_values_cannot_be_rewritten(self):
        from language_ai import validate_result
        for source, replacement in (("https://example.com/a", "https://example.com/b"),
                                    (r"C:\art\cover.png", r"C:\art\image.png"),
                                    ("#00D0FF", "#00D1FF"), ("UE5 RGB", "UE6 RGB"),
                                    ("16:9", "4:3"), ("25", "26")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_result("Use " + source + ".", "Use " + replacement + ".")

    def test_paragraph_and_attachment_structure_is_preserved(self):
        from language_ai import validate_result
        for source, replacement in (("First\nSecond", "First Second"),
                                    ("Photo \ufffc here.", "Photo here.")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_result(source, replacement)


if __name__ == "__main__":
    unittest.main()
