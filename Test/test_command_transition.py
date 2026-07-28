#!/usr/bin/env python3
"""Smoke test for Letter Smith directional Command fades and hover tab switching."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets, QtTest

from anima import FX, TabSwitcher


def wait_until(predicate, timeout_ms: int) -> bool:
    elapsed = 0
    while elapsed < timeout_ms:
        if predicate():
            return True
        QtTest.QTest.qWait(20)
        elapsed += 20
    return predicate()


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    window = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(window)

    window.tabbar = QtWidgets.QTabBar()
    for title in ("Images", "Sound", "Command"):
        window.tabbar.addTab(title)
    layout.addWidget(window.tabbar)

    stack = QtWidgets.QStackedWidget()
    for object_name in ("ImagesTab", "SoundTab", "CommandTab"):
        page = QtWidgets.QWidget()
        page.setObjectName(object_name)
        QtWidgets.QLabel(object_name, page).move(24, 24)
        if object_name == "CommandTab":
            page.setProperty("anima.Transition", "command-fade")
            page.setProperty("anima.TransitionDurationMultiplier", 2.0)
        stack.addWidget(page)
    layout.addWidget(stack)

    window.resize(720, 500)
    window.show()
    app.processEvents()

    switcher = TabSwitcher(stack)
    window.tabbar.currentChanged.connect(switcher.go_to)

    assert bool(window.tabbar.property("anima.HoverTabSwitchInstalled")), (
        "Hover tab switching was not installed."
    )

    switcher.go_to(2)
    app.processEvents()
    assert stack.currentIndex() == 2
    assert switcher._active is not None
    assert switcher._active.duration() == int(FX.TAB_MS * 2)
    assert switcher._active.animationCount() == 1
    enter_animation = switcher._active.animationAt(0)
    assert enter_animation.property("anima.CommandFadeDirection") == "in"
    assert float(enter_animation.startValue()) == 0.0
    assert float(enter_animation.endValue()) == 1.0
    assert wait_until(lambda: switcher._active is None, FX.TAB_MS * 2 + 500)

    switcher.go_to(0)
    app.processEvents()
    assert stack.currentIndex() == 0
    assert switcher._active is not None
    assert switcher._active.duration() == int(FX.TAB_MS * 2)
    assert switcher._active.animationCount() == 1
    leave_animation = switcher._active.animationAt(0)
    assert leave_animation.property("anima.CommandFadeDirection") == "out"
    assert float(leave_animation.startValue()) == 1.0
    assert float(leave_animation.endValue()) == 0.0

    # Interrupt the Command fade. The previous group must be canceled cleanly.
    QtTest.QTest.qWait(40)
    switcher.go_to(1)
    app.processEvents()
    assert wait_until(lambda: switcher._active is None, FX.TAB_MS + 500)
    assert stack.currentIndex() == 1
    assert stack.currentWidget().isVisible()

    # Exercise the restored hover-to-switch behavior.
    window.tabbar.setCurrentIndex(0)
    app.processEvents()
    QtTest.QTest.mouseMove(window.tabbar, window.tabbar.tabRect(2).center())
    assert wait_until(
        lambda: window.tabbar.currentIndex() == 2,
        FX.TAB_HOVER_DELAY_MS + 500,
    )

    print("PASS: directional Command fade-in/fade-out, interruption handling, and hover switching")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
