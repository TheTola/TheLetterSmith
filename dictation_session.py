"""Windows lock/suspend notifications for microphone lifetime."""
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication, QWidget


class DictationSessionMonitor(QAbstractNativeEventFilter):
    def __init__(self, stop):
        super().__init__()
        self.stop = stop
        self.locked = False
        self.available = False
        self.session_registered = False
        self.power_registration = None
        self.window = None
        self.hwnd = 0
        app = QApplication.instance()
        if sys.platform != "win32" or app.platformName() != "windows":
            return
        import ctypes
        from ctypes import wintypes
        self.window = QWidget()
        self.window.setWindowTitle("LetterSmith dictation session monitor")
        self.hwnd = int(self.window.winId())
        try:
            self.api = ctypes.WinDLL("wtsapi32", use_last_error=True)
            self.api.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            self.api.WTSRegisterSessionNotification.restype = wintypes.BOOL
            self.api.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            self.api.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
            self.session_registered = bool(self.api.WTSRegisterSessionNotification(self.hwnd, 0))
            self.power_api = ctypes.WinDLL("user32", use_last_error=True)
            self.power_api.RegisterSuspendResumeNotification.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.power_api.RegisterSuspendResumeNotification.restype = wintypes.HANDLE
            self.power_api.UnregisterSuspendResumeNotification.argtypes = [wintypes.HANDLE]
            self.power_api.UnregisterSuspendResumeNotification.restype = wintypes.BOOL
            self.power_registration = self.power_api.RegisterSuspendResumeNotification(self.hwnd, 0)
            self.available = self.session_registered and bool(self.power_registration)
        except (OSError, AttributeError):
            self.available = False
        app.installNativeEventFilter(self)

    def session_event(self, locked):
        self.locked = locked
        if locked:
            self.stop()

    def nativeEventFilter(self, event_type, message):
        if sys.platform == "win32":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.hWnd == self.hwnd:
                if msg.message == 0x02B1 and msg.wParam in (1, 2, 3, 4, 5, 6, 7, 8):
                    self.session_event(msg.wParam in (2, 4, 6, 7))
                elif msg.message == 0x0218 and msg.wParam in (4, 7, 18):
                    self.session_event(msg.wParam == 4)
        return False, 0

    def close(self):
        QApplication.instance().removeNativeEventFilter(self)
        if self.session_registered:
            self.api.WTSUnRegisterSessionNotification(self.hwnd)
            self.session_registered = False
        if self.power_registration:
            self.power_api.UnregisterSuspendResumeNotification(self.power_registration)
            self.power_registration = None
        if self.window:
            self.window.close()
            self.window.deleteLater()
            self.window = None
        self.available = False
