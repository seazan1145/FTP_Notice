from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone

from .db import MonitorDatabase
from .ftp_client import FtpClient, FtpConnectTimeoutError, FtpDataConnectionTlsError
from .models import AppConfig, FtpConnectionConfig, RemoteFileInfo
from .notifier import NotificationService
from .time_utils import normalize_ftp_datetime, parse_ftp_datetime, to_utc_isoformat


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value)


class MonitorService:
    def __init__(self, config: AppConfig, db: MonitorDatabase, notifier: NotificationService, logger: logging.Logger) -> None:
        self.config = config
        self.db = db
        self.notifier = notifier
        self.logger = logger
        self._first_scan_completed = False

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
        self._first_scan_completed = True

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
        except socket.gaierror as exc:
            self.logger.error("Host name could not be resolved: %s", connection.host)
            self.logger.error(
                "This usually means the host is invalid, misspelled, or still set to the sample value."
            )
            self.logger.info("DNS/name resolution error detail: %s", exc)
            self.logger.debug("Name resolution traceback", exc_info=True)
        except FtpConnectTimeoutError as exc:
            self.logger.error("Connection timeout: %s", connection.display_name)
            self.logger.error("%s", exc)
            self.logger.debug("Connection timeout traceback", exc_info=True)
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
                    "modified_at": file_info.modified_at,
                }
            )
            self.logger.info("Candidate inserted: path=%s size=%s", file_info.remote_path, file_info.file_size)
            if not self.config.startup.notify_existing_on_start and not self._first_scan_completed:
                self.logger.info("Startup existing file registered without notification: path=%s", file_info.remote_path)
            return (True, False)

        old_size = int(row["file_size"] or 0)
        old_modified_at = row["modified_at"]
        size_changed = old_size != file_info.file_size
        modified_changed = (old_modified_at or "") != (file_info.modified_at or "") and bool(file_info.modified_at)
        changed = size_changed or modified_changed

        if changed:
            self.logger.info(
                "Existing file changed: path=%s size_changed=%s modified_changed=%s",
                file_info.remote_path,
                size_changed,
                modified_changed,
            )

        rearm_notification = int(row["is_notified"]) == 1 and changed
        now = datetime.now(timezone.utc)
        last_change = _parse_iso(row["last_size_change_at"])

        if changed:
            stable_age = 0.0
            is_stable = False
        else:
            stable_age = (now - last_change).total_seconds()
            is_stable = stable_age >= self.config.general.stable_seconds

        self.db.update_seen(
            int(row["id"]),
            file_info.file_size,
            file_info.modified_at,
            size_changed=size_changed,
            modified_changed=modified_changed,
            is_stable=is_stable,
            rearm_notification=rearm_notification,
        )

        if rearm_notification:
            self.logger.info(
                "Re-armed candidate due to change: path=%s old_size=%s new_size=%s",
                file_info.remote_path,
                old_size,
                file_info.file_size,
            )

        if changed:
            self.logger.info(
                "Candidate waiting stable after change: path=%s stable_seconds=%s",
                file_info.remote_path,
                self.config.general.stable_seconds,
            )
            return (False, False)

        if int(row["is_notified"]) == 1 and not changed:
            self.logger.info("Skip already notified: path=%s", file_info.remote_path)
            return (False, False)

        if not is_stable:
            self.logger.info(
                "Candidate waiting stable: path=%s elapsed=%s stable_seconds=%s",
                file_info.remote_path,
                int(stable_age),
                self.config.general.stable_seconds,
            )
            return (False, False)

        self.logger.info("Candidate stable, sending notification: path=%s", file_info.remote_path)
        payload = self._build_notice_payload(file_info)
        self.logger.info("Notification dispatch: mode=%s path=%s", self.config.notification.mode, file_info.remote_path)
        ok = self.notifier.send_update(connection.display_name, file_info, payload)
        if ok:
            self.db.mark_notified(int(row["id"]))
            self.logger.info("Marked notified: path=%s", file_info.remote_path)
            return (False, True)

        self.logger.error("Notification failed, mark_notified skipped: path=%s", file_info.remote_path)
        return (False, False)

    def _build_notice_payload(self, file_info: RemoteFileInfo) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat()
        parsed_modified_at = parse_ftp_datetime(file_info.modified_at)
        if parsed_modified_at is None:
            normalized_last_modified = normalize_ftp_datetime(file_info.modified_at)
            self.logger.warning(
                "Failed to parse modified_at, fallback to now: raw=%s path=%s",
                file_info.modified_at,
                file_info.remote_path,
            )
        else:
            normalized_last_modified = to_utc_isoformat(parsed_modified_at)
            self.logger.info(
                "Normalized modified_at: raw=%s normalized=%s path=%s",
                file_info.modified_at,
                normalized_last_modified,
                file_info.remote_path,
            )
        hash_key = f"{file_info.remote_path}_{file_info.file_size}_{normalized_last_modified}"
        return {
            "path": file_info.remote_path,
            "fileName": file_info.file_name,
            "folder": file_info.remote_dir,
            "lastModified": normalized_last_modified,
            "size": file_info.file_size,
            "status": "updated",
            "lastChecked": now_iso,
            "hashKey": hash_key,
        }

    def _matches_filters(self, connection: FtpConnectionConfig, file_info: RemoteFileInfo) -> bool:
        lower_name = file_info.file_name.lower()
        for token in connection.exclude_name_contains:
            if token.lower() in lower_name:
                self.logger.info("Skip by filter: path=%s reason=exclude_name_contains:%s", file_info.remote_path, token)
                return False

        ext = lower_name.rsplit(".", 1)[-1] if "." in lower_name else ""
        if connection.include_extensions and ext not in connection.include_extensions:
            self.logger.info("Skip by filter: path=%s reason=include_extensions", file_info.remote_path)
            return False
        if connection.exclude_extensions and ext in connection.exclude_extensions:
            self.logger.info("Skip by filter: path=%s reason=exclude_extensions", file_info.remote_path)
            return False
        return True
