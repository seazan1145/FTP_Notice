from __future__ import annotations

import logging


class WindowsNotifier:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._toaster = None
        self.backend_name = "logged-only"
        self.available = False
        try:
            from win10toast import ToastNotifier  # type: ignore

            self._toaster = ToastNotifier()
            self.backend_name = "win10toast"
            self.available = True
            self.logger.info("Notification backend initialized: %s", self.backend_name)
        except Exception:
            self.logger.warning("Notification backend unavailable: win10toast not installed or failed to initialize.")
            self.logger.warning("Notification backend initialized: %s", self.backend_name)

    def send_windows_notification(self, title: str, message: str) -> bool:
        if self._toaster is None:
            self.logger.error("Notification not sent (backend=%s): %s | %s", self.backend_name, title, message)
            return False
        try:
            self._toaster.show_toast(title, message, duration=8, threaded=True)
            self.logger.info("Notification sent via backend=%s", self.backend_name)
            return True
        except Exception:
            self.logger.exception("Failed to send Windows notification via backend=%s", self.backend_name)
            return False
