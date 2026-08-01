from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets

import command


class CommandInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (
            QtWidgets.QApplication.instance()
            or QtWidgets.QApplication([])
        )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.command_tab = command.CommandTab(
            Path(self.temp_dir.name)
        )
        self.command_tab.resize(900, 600)
        self.command_tab.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.command_tab.close()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def _drain_events(self) -> None:
        for _ in range(3):
            self.app.processEvents(
                QtCore.QEventLoop.AllEvents,
                50,
            )

    def test_rapid_activation_runs_once_and_restores_idle(self) -> None:
        calls = []

        def succeed(*args, **kwargs):
            calls.append((args, kwargs))
            return True

        with mock.patch.object(
            command,
            "_perform_confirmed_reset",
            side_effect=succeed,
        ):
            self.command_tab._do_reset()
            dialog = self.command_tab._confirm_dialog
            self.command_tab._do_reset()

            self.assertIsNotNone(dialog)
            self.assertIs(self.command_tab._confirm_dialog, dialog)
            self.assertEqual(
                self.command_tab._interaction_state,
                "confirming",
            )
            self.assertFalse(self.command_tab.go_btn.isEnabled())

            dialog.accept()
            self._drain_events()

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.command_tab._interaction_state, "idle")
        self.assertTrue(self.command_tab.go_btn.isEnabled())
        self.assertFalse(self.command_tab.go_btn._busy)
        self.assertEqual(
            self.command_tab.go_btn._activity_anim.state(),
            QtCore.QAbstractAnimation.Stopped,
        )
        self.assertEqual(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

    def test_cancel_does_not_begin_reset(self) -> None:
        with (
            mock.patch.object(
                command,
                "_perform_confirmed_reset",
            ) as perform,
            mock.patch.object(command, "_toast"),
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.reject()
            self._drain_events()

        perform.assert_not_called()
        self.assertEqual(self.command_tab._interaction_state, "idle")
        self.assertTrue(self.command_tab.go_btn.isEnabled())

    def test_failure_is_reported_and_restores_control(self) -> None:
        messages = []

        def fail(*args, **kwargs):
            raise OSError("simulated read-only project")

        def record_toast(_parent, text, **kwargs):
            messages.append(text)

        with (
            mock.patch.object(
                command,
                "_perform_confirmed_reset",
                side_effect=fail,
            ),
            mock.patch.object(command.LOGGER, "exception"),
            mock.patch.object(
                command,
                "_toast",
                side_effect=record_toast,
            ),
        ):
            self.command_tab._do_reset()
            self.command_tab._confirm_dialog.accept()
            self._drain_events()

        self.assertEqual(
            messages,
            ["Wipe failed: simulated read-only project"],
        )
        self.assertEqual(self.command_tab._interaction_state, "idle")
        self.assertTrue(self.command_tab.go_btn.isEnabled())
        self.assertFalse(self.command_tab.go_btn._busy)
        self.assertEqual(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

    def test_reduced_motion_uses_static_busy_state(self) -> None:
        self.command_tab.go_btn._animations_enabled = False

        self.command_tab.go_btn.set_busy(True)
        self.assertEqual(
            self.command_tab.go_btn._activity_anim.state(),
            QtCore.QAbstractAnimation.Stopped,
        )
        self.assertGreater(
            self.command_tab.go_btn._opacity_effect.opacity(),
            0.7,
        )
        self.assertLess(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

        self.command_tab.go_btn.set_busy(False)
        self.assertEqual(
            self.command_tab.go_btn._opacity_effect.opacity(),
            1.0,
        )

    def test_confirmed_reset_emits_wiped_once(self) -> None:
        wiped = []
        self.command_tab.wiped.connect(
            lambda: wiped.append(True)
        )

        with (
            mock.patch.object(
                command,
                "reset_everything",
                return_value=(3, 0),
            ),
            mock.patch.object(command, "_toast"),
        ):
            self.assertTrue(
                command._perform_confirmed_reset(
                    self.command_tab
                )
            )

        self.assertEqual(wiped, [True])


if __name__ == "__main__":
    unittest.main()
