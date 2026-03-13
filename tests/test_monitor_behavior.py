from __future__ import annotations

import logging
import ssl
import unittest
from datetime import datetime, timedelta, timezone

from app.ftp_client import FtpClient
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
    def __init__(self, row: dict):
        self.row = row
        self.marked = False
        self.updated: list[tuple[int, int, bool, bool]] = []

    def get_observed_file(self, connection_name: str, remote_path: str):
        return self.row

    def insert_candidate(self, payload: dict):
        raise AssertionError("insert_candidate should not be called")

    def update_seen(self, record_id: int, file_size: int, size_changed: bool, is_stable: bool):
        self.updated.append((record_id, file_size, size_changed, is_stable))

    def mark_notified(self, record_id: int):
        self.marked = True


class FakeNotifier:
    def __init__(self, result: bool):
        self.result = result

    def send_windows_notification(self, title: str, message: str) -> bool:
        return self.result


class MonitorServiceTests(unittest.TestCase):
    def _build_config(self) -> AppConfig:
        general = GeneralConfig(stable_seconds=30)
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

    def test_notification_failure_does_not_mark_notified(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": 1,
            "file_size": 100,
            "is_notified": 0,
            "last_size_change_at": (now - timedelta(seconds=120)).isoformat(),
            "first_seen_at": (now - timedelta(days=1)).isoformat(),
        }
        db = FakeDB(row)
        service = MonitorService(self._build_config(), db, FakeNotifier(False), logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100)

        service.process_file(conn, info)

        self.assertFalse(db.marked)

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
        notifier = FakeNotifier(True)
        service = MonitorService(self._build_config(), db, notifier, logging.getLogger("test"))
        conn = service.config.connections[0]
        info = RemoteFileInfo("test", "/upload", "/upload/file.txt", "file.txt", 100)

        service.process_file(conn, info)

        self.assertFalse(db.marked)
        self.assertTrue(db.updated)
        self.assertFalse(db.updated[0][3])


if __name__ == "__main__":
    unittest.main()
