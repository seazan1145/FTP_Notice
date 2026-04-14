from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Callable

from .models import MailConfig, NotificationConfig, RemoteFileInfo


class WindowsNotifier:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._toaster = None
        self.backend_name = "logged-only"
        self.available = False
        self.required_package = "win10toast"
        try:
            from win10toast import ToastNotifier  # type: ignore

            self._toaster = ToastNotifier()
            self.backend_name = "win10toast"
            self.available = True
            self.logger.info("Notification backend initialized: %s", self.backend_name)
        except Exception:
            self.logger.warning("Notification backend unavailable: failed to initialize '%s'.", self.required_package)
            self.logger.warning("Install dependency to enable notifications: pip install %s", self.required_package)
            self.logger.warning("Notifications will NOT be displayed until the package is installed.")
            self.logger.warning("Notification backend initialized: %s", self.backend_name)

    def send_windows_notification(self, title: str, message: str) -> bool:
        if self._toaster is None:
            self.logger.error("Notification not sent (backend=%s): %s | %s", self.backend_name, title, message)
            self.logger.error("Notification dependency missing. Install package: %s", self.required_package)
            return False
        try:
            self._toaster.show_toast(title, message, duration=8, threaded=True)
            self.logger.info("Notification sent via backend=%s", self.backend_name)
            return True
        except Exception:
            self.logger.exception("Failed to send Windows notification via backend=%s", self.backend_name)
            return False


class MailNotifier:
    def __init__(self, config: MailConfig, module_path: str, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.module_path = module_path
        self._send_func: Callable[[dict, MailConfig], bool] | None = None
        self._initialize_mail_module()

    def _initialize_mail_module(self) -> None:
        path_text = (self.module_path or "mail.py").strip()
        try:
            module = self._load_module(path_text)
            send_func = getattr(module, "send_ftp_notice", None)
            if not callable(send_func):
                raise AttributeError("send_ftp_notice(data: dict, config: MailConfig) not found")
            self._send_func = send_func
            self.logger.info("Mail notifier initialized: module=%s", path_text)
            self.logger.info(
                "Mail transport: provider=%s smtp=%s:%s tls=%s",
                self.config.provider,
                self.config.smtp_server,
                self.config.smtp_port,
                self.config.use_tls,
            )
            self.logger.info("Mail routing: from=%s to=%s", self.config.from_address, self.config.to_address)
        except Exception:
            self.logger.exception("Mail notifier initialization failed: module=%s", path_text)

    def _load_module(self, path_text: str):
        if path_text.endswith(".py"):
            path = Path(path_text)
            if not path.is_absolute():
                path = Path.cwd() / path
            spec = importlib.util.spec_from_file_location("ftp_notice_mail_module", path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Failed to load mail module from path: {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        return importlib.import_module(path_text)

    def send_update(self, payload: dict) -> bool:
        if not self._send_func:
            self.logger.error("Mail notifier is unavailable: send function not initialized")
            return False

        self.logger.info("MailNotifier.send_update called: path=%s", payload.get("path"))
        try:
            ok = bool(self._send_func(payload, self.config))
            if ok:
                self.logger.info("Mail sent: path=%s", payload.get("path"))
                return True
            self.logger.error("Mail send failed: path=%s", payload.get("path"))
            return False
        except Exception:
            self.logger.exception("Mail send failed: path=%s", payload.get("path"))
            return False


class NotificationService:
    def __init__(
        self,
        notification: NotificationConfig,
        mail: MailConfig,
        windows_notifier: WindowsNotifier,
        mail_notifier: MailNotifier,
        logger: logging.Logger,
    ) -> None:
        self.notification = notification
        self.mail = mail
        self.windows_notifier = windows_notifier
        self.mail_notifier = mail_notifier
        self.logger = logger

    def send_update(self, connection_name: str, file_info: RemoteFileInfo, payload: dict) -> bool:
        mode = self.notification.mode
        label = "新規フォルダ" if file_info.entry_type == "folder" else "FTP新着ファイル"
        win_message = f"[{connection_name}]\n{file_info.remote_dir}\n{file_info.file_name}"

        if mode == "windows":
            return self.windows_notifier.send_windows_notification(label, win_message)
        if mode == "mail":
            return self.mail_notifier.send_update(payload)
        if mode == "both":
            win_ok = self.windows_notifier.send_windows_notification(label, win_message)
            mail_ok = self.mail_notifier.send_update(payload)
            both_ok = win_ok and mail_ok
            if not both_ok:
                self.logger.error(
                    "Both-mode notification incomplete: windows_ok=%s mail_ok=%s path=%s",
                    win_ok,
                    mail_ok,
                    file_info.remote_path,
                )
            return both_ok

        self.logger.error("Unknown notification mode: %s", mode)
        return False
