from __future__ import annotations

import logging


class WindowsNotifier:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._toaster = None
        try:
            from win10toast import ToastNotifier  # type: ignore

            self._toaster = ToastNotifier()
        except Exception:
            self.logger.warning("win10toast is unavailable. Notifications are logged only.")

    def send_windows_notification(self, title: str, message: str) -> bool:
        if self._toaster is None:
            self.logger.info("NOTIFY %s | %s", title, message)
            return False
        try:
            self._toaster.show_toast(title, message, duration=8, threaded=True)
            return True
        except Exception:
            self.logger.exception("Failed to send Windows notification")
            return False
