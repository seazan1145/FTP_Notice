from __future__ import annotations

import importlib
import importlib.util
import json
import logging
from pathlib import Path
from typing import Callable

from .models import GeneralConfig, RemoteFileInfo


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
    def __init__(self, config: GeneralConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._send_func: Callable[[dict], bool] | None = None
        self._initialize_mail_module()

    def _initialize_mail_module(self) -> None:
        path_text = (self.config.mail_module_path or "mail.py").strip()
        try:
            module = self._load_module(path_text)
            configure_func = getattr(module, "configure_mail", None)
            if callable(configure_func):
                configure_func(
                    {
                        "mail_enabled": self.config.mail_enabled,
                        "mail_smtp_server": self.config.mail_smtp_server,
                        "mail_smtp_port": self.config.mail_smtp_port,
                        "mail_from_address": self.config.mail_from_address,
                        "mail_to_address": self.config.mail_to_address,
                        "mail_subject": self.config.mail_subject,
                        "mail_use_tls": self.config.mail_use_tls,
                        "mail_username": self.config.mail_username,
                        "mail_password": self.config.mail_password,
                    }
                )
            send_func = getattr(module, "send_ftp_notice", None)
            if not callable(send_func):
                raise AttributeError("send_ftp_notice(data: dict) not found")
            self._send_func = send_func
            self.logger.info("Mail notifier initialized: module=%s", path_text)
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

        self.logger.info("Starting mail send: path=%s size=%s", payload.get("path"), payload.get("size"))
        try:
            ok = bool(self._send_func(payload))
            if ok:
                self.logger.info("Mail send success: path=%s", payload.get("path"))
                return True
            self.logger.error("Mail send failed (function returned false): path=%s", payload.get("path"))
            return False
        except Exception:
            self.logger.exception("Mail send exception: payload=%s", json.dumps(payload, ensure_ascii=False))
            return False


class NotificationService:
    def __init__(self, config: GeneralConfig, windows_notifier: WindowsNotifier, mail_notifier: MailNotifier, logger: logging.Logger) -> None:
        self.config = config
        self.windows_notifier = windows_notifier
        self.mail_notifier = mail_notifier
        self.logger = logger

    def send_update(self, connection_name: str, file_info: RemoteFileInfo, payload: dict) -> bool:
        mode = self.config.notification_mode
        win_message = f"[{connection_name}]\n{file_info.remote_dir}\n{file_info.file_name}"

        if mode == "windows":
            return self.windows_notifier.send_windows_notification("FTP新着ファイル", win_message)
        if mode == "mail":
            return self.mail_notifier.send_update(payload)
        if mode == "both":
            win_ok = self.windows_notifier.send_windows_notification("FTP新着ファイル", win_message)
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
