from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
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


@dataclass(slots=True)
class ConnectionRuntimeState:
    is_connected: bool = False
    consecutive_failures: int = 0
    next_wait_seconds: int = 0
    next_scan_at: float = 0.0
    last_scan_started_at: float = 0.0


class MonitorService:
    def __init__(self, config: AppConfig, db: MonitorDatabase, notifier: NotificationService, logger: logging.Logger) -> None:
        self.config = config
        self.db = db
        self.notifier = notifier
        self.logger = logger
        self._first_scan_completed = False
        self.manual_refresh_event = threading.Event()
        self._runtime_states: dict[str, ConnectionRuntimeState] = {}
        self._clients: dict[str, FtpClient] = {}

    def run_once(self) -> None:
        self.run_pending_scans(force_all=True)

    def request_manual_refresh(self) -> None:
        self.logger.info("Manual refresh requested")
        self.manual_refresh_event.set()

    def run_pending_scans(self, force_all: bool = False) -> int:
        enabled = [c for c in self.config.connections if c.enabled]
        now_monotonic = time.monotonic()
        manual_requested = force_all or self.manual_refresh_event.is_set()
        if manual_requested:
            self.manual_refresh_event.clear()
        self.logger.info("Config loaded: %s connection(s), %s enabled.", len(self.config.connections), len(enabled))
        detected_count = 0
        new_candidate_count = 0
        notified_count = 0
        for connection in enabled:
            state = self._runtime_states.setdefault(connection.section_name, ConnectionRuntimeState())
            if not manual_requested and state.next_scan_at > now_monotonic:
                continue
            if manual_requested:
                self.logger.info("Manual refresh started: %s", connection.display_name)
            conn_detected, conn_new, conn_notified = self.process_connection(connection, state)
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
        waits = [
            max(0, int(state.next_scan_at - time.monotonic()))
            for state in self._runtime_states.values()
            if state.next_scan_at > 0
        ]
        next_wait = min(waits) if waits else self.config.general.poll_interval_seconds
        return max(0, next_wait)

    def process_connection(self, connection: FtpConnectionConfig, state: ConnectionRuntimeState | None = None) -> tuple[int, int, int]:
        if state is None:
            state = self._runtime_states.setdefault(connection.section_name, ConnectionRuntimeState())
        client = self._clients.get(connection.section_name)
        if client is None or not self.config.general.keep_connection_alive:
            client = FtpClient(connection, self.config.general, logger=self.logger)
            if self.config.general.keep_connection_alive:
                self._clients[connection.section_name] = client
        detected_count = 0
        new_candidate_count = 0
        notified_count = 0
        state.last_scan_started_at = time.monotonic()
        try:
            if not client.is_connected:
                self.logger.info("Connecting: %s (%s:%s)", connection.display_name, connection.host, connection.port)
            client.ensure_connected()
            if not state.is_connected:
                self.logger.info("Connected: %s", connection.display_name)
            state.is_connected = True
            for remote_dir in connection.remote_dirs:
                self.logger.info("Scanning: %s %s", connection.display_name, remote_dir)
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
                    raise
                except Exception:
                    self.logger.exception("Failed scanning directory: %s", remote_dir)
                    raise
            if state.consecutive_failures > 0:
                self.logger.info("Scan recovered, resetting backoff: %s", connection.display_name)
            state.consecutive_failures = 0
            state.next_wait_seconds = self.config.general.poll_interval_seconds
            state.next_scan_at = time.monotonic() + state.next_wait_seconds
            self.logger.info("Waiting %ss before next scan: %s", state.next_wait_seconds, connection.display_name)
        except socket.gaierror as exc:
            self.logger.error("Host name could not be resolved: %s", connection.host)
            self.logger.error(
                "This usually means the host is invalid, misspelled, or still set to the sample value."
            )
            self.logger.info("DNS/name resolution error detail: %s", exc)
            self.logger.debug("Name resolution traceback", exc_info=True)
            self._handle_failure(connection, client, state, "connect")
        except FtpConnectTimeoutError as exc:
            self.logger.error("Connection timeout: %s", connection.display_name)
            self.logger.error("%s", exc)
            self.logger.debug("Connection timeout traceback", exc_info=True)
            self._handle_failure(connection, client, state, "connect")
        except Exception:
            if state.is_connected:
                self.logger.exception("Scan failed: %s error=%s", connection.display_name, "connection_lost_or_scan_error")
            else:
                self.logger.exception("Connection failed: %s", connection.display_name)
            self._handle_failure(connection, client, state, "scan")
        finally:
            if not self.config.general.keep_connection_alive:
                client.disconnect()
                state.is_connected = False
        return (detected_count, new_candidate_count, notified_count)

    def _handle_failure(self, connection: FtpConnectionConfig, client: FtpClient, state: ConnectionRuntimeState, phase: str) -> None:
        state.consecutive_failures += 1
        state.is_connected = False
        if self.config.general.reconnect_on_error:
            client.disconnect()
            self.logger.warning("Connection lost, reconnecting: %s", connection.display_name)
        state.next_wait_seconds = self._compute_next_wait_seconds(state.consecutive_failures)
        state.next_scan_at = time.monotonic() + state.next_wait_seconds
        self.logger.warning(
            "Reconnect scheduled after %ss (failure_count=%s) [%s]",
            state.next_wait_seconds,
            state.consecutive_failures,
            phase,
        )

    def _compute_next_wait_seconds(self, failure_count: int) -> int:
        if not self.config.general.backoff_enabled:
            return self.config.general.poll_interval_seconds
        schedule = self.config.general.backoff_schedule_seconds
        index = min(max(failure_count - 1, 0), len(schedule) - 1)
        return schedule[index]

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
