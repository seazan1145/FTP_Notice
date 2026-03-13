from __future__ import annotations

import logging
from datetime import datetime, timezone

from .db import MonitorDatabase
from .ftp_client import FtpClient, FtpDataConnectionTlsError
from .models import AppConfig, FtpConnectionConfig, RemoteFileInfo
from .notifier import WindowsNotifier


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value)


class MonitorService:
    def __init__(self, config: AppConfig, db: MonitorDatabase, notifier: WindowsNotifier, logger: logging.Logger) -> None:
        self.config = config
        self.db = db
        self.notifier = notifier
        self.logger = logger

    def run_once(self) -> None:
        enabled = [c for c in self.config.connections if c.enabled]
        self.logger.info("Config loaded: %s connection(s), %s enabled.", len(self.config.connections), len(enabled))
        detected_count = 0
        new_candidate_count = 0
        notified_count = 0
        for connection in enabled:
            conn_detected, conn_new, conn_notified = self.process_connection(connection)
            detected_count += conn_detected
            new_candidate_count += conn_new
            notified_count += conn_notified
        self.logger.info(
            "Scan summary: detected=%s new_candidates=%s notified=%s",
            detected_count,
            new_candidate_count,
            notified_count,
        )

    def process_connection(self, connection: FtpConnectionConfig) -> tuple[int, int, int]:
        client = FtpClient(connection, self.config.general, logger=self.logger)
        detected_count = 0
        new_candidate_count = 0
        notified_count = 0
        try:
            self.logger.info("Connecting: %s (%s:%s)", connection.display_name, connection.host, connection.port)
            client.connect()
            self.logger.info("Connected: %s", connection.display_name)
            for remote_dir in connection.remote_dirs:
                self.logger.info("Scanning: %s", remote_dir)
                try:
                    files = client.list_files(remote_dir, recursive=connection.recursive)
                    self.logger.info("Directory scan result: dir=%s detected=%s", remote_dir, len(files))
                    for file_info in files:
                        detected_count += 1
                        is_new, notified = self.process_file(connection, file_info)
                        if is_new:
                            new_candidate_count += 1
                        if notified:
                            notified_count += 1
                except FtpDataConnectionTlsError:
                    self.logger.exception("Failed scanning directory due to FTPS data connection TLS/session issue: %s", remote_dir)
                except Exception:
                    self.logger.exception("Failed scanning directory: %s", remote_dir)
        except Exception:
            self.logger.exception("Connection failed: %s", connection.display_name)
        finally:
            client.disconnect()
        return (detected_count, new_candidate_count, notified_count)

    def process_file(self, connection: FtpConnectionConfig, file_info: RemoteFileInfo) -> tuple[bool, bool]:
        if not self._matches_filters(connection, file_info):
            return (False, False)

        row = self.db.get_observed_file(connection.display_name, file_info.remote_path)
        if row is None:
            self.db.insert_candidate(
                {
                    "connection_name": connection.display_name,
                    "remote_dir": file_info.remote_dir,
                    "remote_path": file_info.remote_path,
                    "file_name": file_info.file_name,
                    "file_size": file_info.file_size,
                }
            )
            self.logger.info("New candidate detected: %s size=%s", file_info.remote_path, file_info.file_size)
            return (True, False)

        if int(row["is_notified"]) == 1:
            self.logger.debug("Skipping already notified file: %s", file_info.remote_path)
            return (False, False)

        old_size = int(row["file_size"] or 0)
        size_changed = old_size != file_info.file_size
        now = datetime.now(timezone.utc)
        last_change = _parse_iso(row["last_size_change_at"])

        stable_age = (now - last_change).total_seconds()
        is_stable = (not size_changed) and stable_age >= self.config.general.stable_seconds

        self.db.update_seen(int(row["id"]), file_info.file_size, size_changed=size_changed, is_stable=is_stable)

        if size_changed:
            self.logger.info("Size changed: %s old=%s new=%s", file_info.remote_path, old_size, file_info.file_size)
            return (False, False)

        if is_stable:
            message = f"[{connection.display_name}]\n{file_info.remote_dir}\n{file_info.file_name}"
            ok = self.notifier.send_windows_notification("FTP新着ファイル", message)
            if ok:
                self.db.mark_notified(int(row["id"]))
                self.logger.info("Notification sent: %s", file_info.remote_path)
                return (False, True)

            self.logger.error(
                "Notification failed, keeping record as unnotified: %s",
                file_info.remote_path,
            )
        return (False, False)

    def _matches_filters(self, connection: FtpConnectionConfig, file_info: RemoteFileInfo) -> bool:
        lower_name = file_info.file_name.lower()
        for token in connection.exclude_name_contains:
            if token.lower() in lower_name:
                self.logger.debug("Skipping file by exclude_name_contains: %s token=%s", file_info.remote_path, token)
                return False

        ext = lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""
        if connection.include_extensions and ext not in connection.include_extensions:
            self.logger.debug("Skipping file by include_extensions: %s ext=%s", file_info.remote_path, ext)
            return False
        if connection.exclude_extensions and ext in connection.exclude_extensions:
            self.logger.debug("Skipping file by exclude_extensions: %s ext=%s", file_info.remote_path, ext)
            return False
        return True
