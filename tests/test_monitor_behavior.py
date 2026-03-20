from __future__ import annotations

import logging
import socket
import ssl
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ftp_client import FtpClient, FtpConnectTimeoutError
from app.models import AppConfig, FtpConnectionConfig, GeneralConfig, RemoteFileInfo
from app.monitor import MonitorService


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
            protocol="ftps",
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
        self.updated: list[tuple[int, int, bool, bool]] = []
        self.inserted = False

    def get_observed_file(self, connection_name: str, remote_path: str):
        return self.row

    def insert_candidate(self, payload: dict):
        self.inserted = True

    def update_seen(self, record_id: int, file_size: int, size_changed: bool, is_stable: bool):
        self.updated.append((record_id, file_size, size_changed, is_stable))

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
    def _build_config(self, notification_mode: str = "windows") -> AppConfig:
        general = GeneralConfig(stable_seconds=30, notification_mode=notification_mode)
        conn = FtpConnectionConfig(
            section_name="ftp_01",
            enabled=True,
            display_name="test",
            protocol="ftp",
            host="example.com",
            port=21,
            username="u",
            password="p",
            remote_dirs=["/upload"],
        )
        return AppConfig(general=general, connections=[conn], root_dir=None, db_path=None)  # type: ignore[arg-type]

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
        self.assertEqual(notifier.calls, [])

    def test_size_change_does_not_notify(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 90,
            "is_notified": 0,
            "last_size_change_at": (now - timedelta(seconds=120)).isoformat(),
        }
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100)

        is_new, notified = service.process_file(conn, info)

        self.assertFalse(is_new)
        self.assertFalse(notified)
        self.assertFalse(db.marked)
        self.assertTrue(db.updated)
        self.assertTrue(db.updated[0][2])
        self.assertEqual(notifier.calls, [])

    def test_mail_mode_success_marks_notified(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 100,
            "is_notified": 0,
            "last_size_change_at": (now - timedelta(seconds=120)).isoformat(),
        }
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100, modified_at="2025-01-01T00:00:00+00:00")

        _, notified = service.process_file(conn, info)

        self.assertTrue(notified)
        self.assertTrue(db.marked)
        self.assertEqual(len(notifier.calls), 1)
        payload = notifier.calls[0]
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["hashKey"], "/upload/file.txt_100")
        self.assertEqual(payload["lastModified"], "2025-01-01T00:00:00+00:00")

    def test_mail_mode_failure_does_not_mark_notified(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 100,
            "is_notified": 0,
            "last_size_change_at": (now - timedelta(seconds=120)).isoformat(),
        }
        db = FakeDB(row)
        notifier = FakeNotificationService(False)
        service = MonitorService(self._build_config("mail"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100)

        _, notified = service.process_file(conn, info)

        self.assertFalse(notified)
        self.assertFalse(db.marked)
        self.assertEqual(len(notifier.calls), 1)

    def test_stability_uses_last_size_change(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 100,
            "is_notified": 0,
            "last_size_change_at": (now - timedelta(seconds=10)).isoformat(),
            "first_seen_at": (now - timedelta(days=1)).isoformat(),
        }
        db = FakeDB(row)
        notifier = FakeNotificationService(True)
        service = MonitorService(self._build_config("windows"), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100)

        service.process_file(conn, info)

        self.assertFalse(db.marked)
        self.assertTrue(db.updated)
        self.assertFalse(db.updated[0][3])
        self.assertEqual(notifier.calls, [])

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
        config = AppConfig(general=general, connections=[conn], root_dir=Path("."), db_path=Path("monitor.db"))
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
        self.assertIn("確認ポイント", output)


if __name__ == "__main__":
    unittest.main()
