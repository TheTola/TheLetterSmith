from __future__ import annotations

import sys
import unittest

from PySide6 import QtCore, QtGui, QtWidgets

from Message_tab import IDENTITY_LOCK_KEYS, IdentityLineEdit, MessageTab


class IdentityFieldLockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def test_lock_keys_cover_title_recipient_and_url(self) -> None:
        self.assertEqual(
            set(IDENTITY_LOCK_KEYS),
            {"title", "recipient", "published_url"},
        )
        self.assertFalse(MessageTab._setting_bool("false"))
        self.assertTrue(MessageTab._setting_bool("true"))

    def test_double_click_unlocks_a_committed_field(self) -> None:
        field = IdentityLineEdit("A Letter")
        field.setReadOnly(True)
        field.show()
        self.app.processEvents()

        event = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonDblClick,
            field.rect().center(),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        field.mouseDoubleClickEvent(event)
        self.assertFalse(field.isReadOnly())
        field.deleteLater()

    def test_enter_commit_locks_a_non_empty_field(self) -> None:
        message = MessageTab.__new__(MessageTab)
        message.settings = {}
        message.status = QtWidgets.QLabel()
        message._save_settings = lambda: True
        message._persist_settings = lambda *, announce: True
        field = IdentityLineEdit("Recipient")

        message._commit_identity_field(field, "recipient")

        self.assertTrue(field.isReadOnly())
        self.assertTrue(message.settings["recipient_name_locked"])
        self.assertIn("#10263b", field.styleSheet())
        self.assertIn("#78b9dd", field.styleSheet())
        field.deleteLater()

    def test_double_click_clears_the_persisted_lock_for_reediting(self) -> None:
        message = MessageTab.__new__(MessageTab)
        message.settings = {"recipient_title_locked": True}
        field = IdentityLineEdit("A Letter")
        field.setReadOnly(True)

        message._unlock_identity_field(field, "title")

        self.assertFalse(message.settings["recipient_title_locked"])
        self.assertFalse(field.isReadOnly())
        field.deleteLater()


if __name__ == "__main__":
    unittest.main()
