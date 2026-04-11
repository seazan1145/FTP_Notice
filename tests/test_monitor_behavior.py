from __future__ import annotations

import logging
import socket
import ssl
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ftp_client import FtpClient, FtpConnectTimeoutError
from app.models import AppConfig, FtpConnectionConfig, GeneralConfig, MailConfig, NotificationConfig, RemoteFileInfo, StartupConfig
from app.monitor import ConnectionRuntimeState, MonitorService


class FakeFTPForFallback:
    def mlsd(self, target_dir: str):
        raise ssl.SSLEOFError("eof")

    def retrlines(self, command: str, callback):
        callback("-rw-r--r-- 1 user group 123 Apr 10 10:00 file one.txt")


class FtpClientFallbackTests(unittest.TestCase):
    def test_mlsd_failure_falls_back_to_list(self):
        config = FtpConnectionConfig(
            section_name="ftp_01",
            enabled=True,
            display_name="test",
            protocol="ftps-explicit",
            host="example.com",
            port=990,
            username="u",
            password="p",
            remote_dirs=["/upload"],
        )
        client = FtpClient(config, GeneralConfig(), logger=logging.getLogger("test"))
        client.ftp = FakeFTPForFallback()

        files = list(client.list_files("/upload", recursive=False))

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].file_name, "file one.txt")
        self.assertEqual(files[0].file_size, 123)


class FakeDB:
    def __init__(self, row: dict | None):
        self.row = row
        self.marked = False
        self.updated: list[tuple] = []
        self.inserted = False

    def get_observed_file(self, connection_name: str, remote_path: str):
        return self.row

    def insert_candidate(self, payload: dict):
        self.inserted = True

    def update_seen(self, record_id: int, file_size: int, modified_at: str | None, **kwargs):
        self.updated.append((record_id, file_size, modified_at, kwargs))

    def mark_notified(self, record_id: int):
        self.marked = True


class FakeNotificationService:
    def __init__(self, result: bool):
        self.result = result
        self.calls: list[dict] = []

    def send_update(self, connection_name: str, file_info: RemoteFileInfo, payload: dict) -> bool:
        self.calls.append(payload)
        return self.result


class MonitorServiceTests(unittest.TestCase):
    def _build_config(self, mode: str = "windows", notify_existing_on_start: bool = False) -> AppConfig:
        general = GeneralConfig(stable_seconds=30)
        conn = FtpConnectionConfig(
            section_name="ftp_01",
            enabled=True,
            display_name="test",
            protocol="ftp",
            host="real.host.local",
            port=21,
            username="u",
            password="p",
            remote_dirs=["/upload"],
        )
        return AppConfig(
            general=general,
            notification=NotificationConfig(mode=mode),
            mail=MailConfig(),
            startup=StartupConfig(notify_existing_on_start=notify_existing_on_start),
            connections=[conn],
            root_dir=Path("."),
            db_path=Path("monitor.db"),
        )

    def test_new_candidate_inserts_only(self):
        db = FakeDB(None)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/new.txt", "new.txt", 100)

        is_new, notified = service.process_file(conn, info)

        self.assertTrue(is_new)
        self.assertFalse(notified)
        self.assertTrue(db.inserted)
        self.assertFalse(db.marked)

    def test_process_file_stable_then_send(self):
        now = datetime.now(timezone.utc)
        row = {"id": 1, "file_size": 100, "modified_at": None, "is_notified": 0, "last_size_change_at": (now - timedelta(seconds=120)).isoformat()}
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]

        _, notified = service.process_file(conn, RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100))

        self.assertTrue(notified)
        self.assertTrue(db.marked)
        self.assertEqual(len(notifier.calls), 1)

    def test_notified_file_size_changed_rearms(self):
        now = datetime.now(timezone.utc)
        row = {"id": 1, "file_size": 100, "modified_at": None, "is_notified": 1, "last_size_change_at": (now - timedelta(seconds=120)).isoformat()}
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]

        _, notified = service.process_file(conn, RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 101))

        self.assertFalse(notified)
        self.assertFalse(db.marked)
        self.assertTrue(db.updated[-1][3]["rearm_notification"])

    def test_notified_file_modified_changed_rearms(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 100,
            "modified_at": "2025-01-01T00:00:00+00:00",
            "is_notified": 1,
            "last_size_change_at": (now - timedelta(seconds=120)).isoformat(),
        }
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]

        _, notified = service.process_file(
            conn,
            RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100, modified_at="2025-01-01T01:00:00+00:00"),
        )

        self.assertFalse(notified)
        self.assertTrue(db.updated[-1][3]["modified_changed"])

    def test_filter_excluded_no_send(self):
        config = self._build_config("mail")
        config.connections[0].exclude_extensions = ["tmp"]
        db = FakeDB({"id": 1, "file_size": 100, "modified_at": None, "is_notified": 0, "last_size_change_at": datetime.now(timezone.utc).isoformat()})
        notifier = FakeNotificationService(True)
        service = MonitorService(config, db, notifier, logging.getLogger("test"))

        _, notified = service.process_file(config.connections[0], RemoteFileInfo("test", "/upload", "/upload/a.tmp", "a.tmp", 100))

        self.assertFalse(notified)
        self.assertEqual(notifier.calls, [])

    def test_send_failure_does_not_mark_notified(self):
        now = datetime.now(timezone.utc)
        row = {"id": 1, "file_size": 100, "modified_at": None, "is_notified": 0, "last_size_change_at": (now - timedelta(seconds=120)).isoformat()}
        db = FakeDB(row)
        notifier = FakeNotificationService(False)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))

        _, notified = service.process_file(service.config.connections[0], RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100))
        self.assertFalse(notified)
        self.assertFalse(db.marked)

    def test_startup_flag_difference(self):
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100)

        service_false = MonitorService(self._build_config("mail", notify_existing_on_start=False), FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))
        service_false.process_file(service_false.config.connections[0], info)
        self.assertFalse(service_false._first_scan_completed)

        service_true = MonitorService(self._build_config("mail", notify_existing_on_start=True), FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))
        is_new, notified = service_true.process_file(service_true.config.connections[0], info)
        self.assertTrue(is_new)
        self.assertFalse(notified)

    def test_windows_mode_kept(self):
        now = datetime.now(timezone.utc)
        row = {"id": 1, "file_size": 100, "modified_at": None, "is_notified": 0, "last_size_change_at": (now - timedelta(seconds=120)).isoformat()}
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("windows"), db, notifier, logging.getLogger("test"))

        _, notified = service.process_file(service.config.connections[0], RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100))
        self.assertTrue(notified)

    def test_payload_last_modified_normalized_and_hash_uses_normalized_value(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 100,
            "modified_at": "20260318110227.000",
            "is_notified": 0,
            "last_size_change_at": (now - timedelta(seconds=120)).isoformat(),
        }
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))

        _, notified = service.process_file(
            service.config.connections[0],
            RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100, modified_at="20260318110227.000"),
        )

        self.assertTrue(notified)
        payload = notifier.calls[0]
        self.assertEqual(payload["lastModified"], "2026-03-18T11:02:27+00:00")
        self.assertEqual(payload["hashKey"], "/upload/file.txt_100_2026-03-18T11:02:27+00:00")

    def test_invalid_modified_at_fallback_does_not_break_notification(self):
        now = datetime.now(timezone.utc)
        row = {"id": 1, "file_size": 100, "modified_at": "invalid", "is_notified": 0, "last_size_change_at": (now - timedelta(seconds=120)).isoformat()}
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))

        _, notified = service.process_file(
            service.config.connections[0],
            RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100, modified_at="invalid"),
        )

        self.assertTrue(notified)
        payload = notifier.calls[0]
        self.assertIsNotNone(datetime.fromisoformat(payload["lastModified"]))

    def test_process_connection_logs_timeout_with_hint(self):
        general = GeneralConfig(connect_timeout=15)
        conn = FtpConnectionConfig(
            section_name="ftp_01",
            enabled=True,
            display_name="Sunrise FTP",
            protocol="ftps-implicit",
            host="ftps.sunrise-office.net",
            port=990,
            username="u",
            password="p",
            remote_dirs=["/upload"],
        )
        config = AppConfig(
            general=general,
            notification=NotificationConfig(mode="windows"),
            mail=MailConfig(),
            startup=StartupConfig(),
            connections=[conn],
            root_dir=Path("."),
            db_path=Path("monitor.db"),
        )
        service = MonitorService(config, FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))

        original_connect = FtpClient.connect

        def fake_connect(_self: FtpClient) -> None:
            raise FtpConnectTimeoutError(
                host=conn.host,
                port=conn.port,
                protocol=conn.protocol,
                timeout_seconds=general.connect_timeout,
                phase="connect",
                original_exc=socket.timeout("timed out"),
            )

        FtpClient.connect = fake_connect  # type: ignore[method-assign]
        try:
            with self.assertLogs("test", level="ERROR") as logs:
                detected, new_candidates, notified = service.process_connection(conn)
        finally:
            FtpClient.connect = original_connect  # type: ignore[method-assign]

        self.assertEqual((detected, new_candidates, notified), (0, 0, 0))
        output = "\n".join(logs.output)
        self.assertIn("Connection timeout: Sunrise FTP", output)

    def test_process_connection_scans_multiple_directories_with_single_connect(self):
        general = GeneralConfig(connect_timeout=15, keep_connection_alive=False)
        conn = FtpConnectionConfig(
            section_name="ftp_01",
            enabled=True,
            display_name="Sunrise FTP",
            protocol="ftp",
            host="real.host.local",
            port=21,
            username="u",
            password="p",
            remote_dirs=["/to", "/from"],
        )
        config = AppConfig(
            general=general,
            notification=NotificationConfig(mode="windows"),
            mail=MailConfig(),
            startup=StartupConfig(),
            connections=[conn],
            root_dir=Path("."),
            db_path=Path("monitor.db"),
        )
        service = MonitorService(config, FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))

        calls: dict[str, list[str] | int] = {"list_dirs": [], "connect": 0, "disconnect": 0}
        original_connect = FtpClient.connect
        original_disconnect = FtpClient.disconnect
        original_list_files = FtpClient.list_files
        original_process_file = MonitorService.process_file

        def fake_connect(_self: FtpClient) -> None:
            calls["connect"] += 1  # type: ignore[operator]

        def fake_disconnect(_self: FtpClient) -> None:
            calls["disconnect"] += 1  # type: ignore[operator]

        def fake_list_files(_self: FtpClient, remote_dir: str, recursive: bool = False) -> list[RemoteFileInfo]:
            assert recursive is False
            calls["list_dirs"].append(remote_dir)  # type: ignore[union-attr]
            return [RemoteFileInfo("test", remote_dir, f"{remote_dir}/file.txt", "file.txt", 1)]

        def fake_process_file(_self: MonitorService, _connection: FtpConnectionConfig, _file: RemoteFileInfo) -> tuple[bool, bool]:
            return (False, False)

        FtpClient.connect = fake_connect  # type: ignore[method-assign]
        FtpClient.disconnect = fake_disconnect  # type: ignore[method-assign]
        FtpClient.list_files = fake_list_files  # type: ignore[method-assign]
        MonitorService.process_file = fake_process_file  # type: ignore[method-assign]
        try:
            detected, new_candidates, notified = service.process_connection(conn)
        finally:
            FtpClient.connect = original_connect  # type: ignore[method-assign]
            FtpClient.disconnect = original_disconnect  # type: ignore[method-assign]
            FtpClient.list_files = original_list_files  # type: ignore[method-assign]
            MonitorService.process_file = original_process_file  # type: ignore[method-assign]

        self.assertEqual((detected, new_candidates, notified), (2, 0, 0))
        self.assertEqual(calls["connect"], 1)
        self.assertEqual(calls["disconnect"], 1)
        self.assertEqual(calls["list_dirs"], ["/to", "/from"])

    def test_process_connection_marks_failure_when_one_directory_fails(self):
        general = GeneralConfig(connect_timeout=15, keep_connection_alive=False)
        conn = FtpConnectionConfig(
            section_name="ftp_01",
            enabled=True,
            display_name="Sunrise FTP",
            protocol="ftp",
            host="real.host.local",
            port=21,
            username="u",
            password="p",
            remote_dirs=["/to", "/from"],
        )
        config = AppConfig(
            general=general,
            notification=NotificationConfig(mode="windows"),
            mail=MailConfig(),
            startup=StartupConfig(),
            connections=[conn],
            root_dir=Path("."),
            db_path=Path("monitor.db"),
        )
        service = MonitorService(config, FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))

        original_connect = FtpClient.connect
        original_disconnect = FtpClient.disconnect
        original_list_files = FtpClient.list_files
        original_process_file = MonitorService.process_file

        def fake_connect(_self: FtpClient) -> None:
            return None

        def fake_disconnect(_self: FtpClient) -> None:
            return None

        def fake_list_files(_self: FtpClient, remote_dir: str, recursive: bool = False) -> list[RemoteFileInfo]:
            if remote_dir == "/to":
                raise RuntimeError("failed /to")
            return [RemoteFileInfo("test", remote_dir, f"{remote_dir}/file.txt", "file.txt", 1)]

        def fake_process_file(_self: MonitorService, _connection: FtpConnectionConfig, _file: RemoteFileInfo) -> tuple[bool, bool]:
            return (False, False)

        FtpClient.connect = fake_connect  # type: ignore[method-assign]
        FtpClient.disconnect = fake_disconnect  # type: ignore[method-assign]
        FtpClient.list_files = fake_list_files  # type: ignore[method-assign]
        MonitorService.process_file = fake_process_file  # type: ignore[method-assign]
        try:
            with self.assertLogs("test", level="ERROR") as logs:
                detected, new_candidates, notified = service.process_connection(conn)
        finally:
            FtpClient.connect = original_connect  # type: ignore[method-assign]
            FtpClient.disconnect = original_disconnect  # type: ignore[method-assign]
            FtpClient.list_files = original_list_files  # type: ignore[method-assign]
            MonitorService.process_file = original_process_file  # type: ignore[method-assign]

        self.assertEqual((detected, new_candidates, notified), (0, 0, 0))
        self.assertIn("Failed scanning directory: /to", "\n".join(logs.output))

    def test_keep_connection_alive_true_does_not_disconnect_after_success(self):
        conn = self._build_config("windows").connections[0]
        config = self._build_config("windows")
        config.general.keep_connection_alive = True
        service = MonitorService(config, FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))

        calls = {"disconnect": 0}
        original_disconnect = FtpClient.disconnect
        original_ensure_connected = FtpClient.ensure_connected
        original_list_files = FtpClient.list_files

        def fake_disconnect(_self: FtpClient) -> None:
            calls["disconnect"] += 1

        def fake_ensure_connected(_self: FtpClient) -> None:
            return None

        def fake_list_files(_self: FtpClient, remote_dir: str, recursive: bool = False) -> list[RemoteFileInfo]:
            return []

        FtpClient.disconnect = fake_disconnect  # type: ignore[method-assign]
        FtpClient.ensure_connected = fake_ensure_connected  # type: ignore[method-assign]
        FtpClient.list_files = fake_list_files  # type: ignore[method-assign]
        try:
            service.process_connection(conn)
        finally:
            FtpClient.disconnect = original_disconnect  # type: ignore[method-assign]
            FtpClient.ensure_connected = original_ensure_connected  # type: ignore[method-assign]
            FtpClient.list_files = original_list_files  # type: ignore[method-assign]

        self.assertEqual(calls["disconnect"], 0)

    def test_backoff_increases_and_resets_on_success(self):
        config = self._build_config("windows")
        config.general.backoff_enabled = True
        config.general.backoff_schedule_seconds = [10, 20, 30, 60]
        service = MonitorService(config, FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))
        conn = config.connections[0]
        state = ConnectionRuntimeState()

        original_ensure_connected = FtpClient.ensure_connected
        original_list_files = FtpClient.list_files

        def fake_ensure_connected(_self: FtpClient) -> None:
            return None

        def fail_list_files(_self: FtpClient, remote_dir: str, recursive: bool = False) -> list[RemoteFileInfo]:
            raise RuntimeError("scan fail")

        FtpClient.ensure_connected = fake_ensure_connected  # type: ignore[method-assign]
        FtpClient.list_files = fail_list_files  # type: ignore[method-assign]
        try:
            service.process_connection(conn, state)
            self.assertEqual(state.consecutive_failures, 1)
            self.assertEqual(state.next_wait_seconds, 10)
            service.process_connection(conn, state)
            self.assertEqual(state.consecutive_failures, 2)
            self.assertEqual(state.next_wait_seconds, 20)
        finally:
            FtpClient.ensure_connected = original_ensure_connected  # type: ignore[method-assign]
            FtpClient.list_files = original_list_files  # type: ignore[method-assign]

        def ok_list_files(_self: FtpClient, remote_dir: str, recursive: bool = False) -> list[RemoteFileInfo]:
            return []

        FtpClient.ensure_connected = fake_ensure_connected  # type: ignore[method-assign]
        FtpClient.list_files = ok_list_files  # type: ignore[method-assign]
        try:
            service.process_connection(conn, state)
        finally:
            FtpClient.ensure_connected = original_ensure_connected  # type: ignore[method-assign]
            FtpClient.list_files = original_list_files  # type: ignore[method-assign]

        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.next_wait_seconds, config.general.poll_interval_seconds)

    def test_manual_refresh_event_triggers_immediate_scan(self):
        config = self._build_config("windows")
        service = MonitorService(config, FakeDB(None), FakeNotificationService(True), logging.getLogger("test"))

        calls = {"count": 0}
        original_process_connection = MonitorService.process_connection

        def fake_process_connection(_self: MonitorService, _connection: FtpConnectionConfig, state=None):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            return (0, 0, 0)

        MonitorService.process_connection = fake_process_connection  # type: ignore[method-assign]
        try:
            service.request_manual_refresh()
            service.run_pending_scans()
        finally:
            MonitorService.process_connection = original_process_connection  # type: ignore[method-assign]

        self.assertEqual(calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
