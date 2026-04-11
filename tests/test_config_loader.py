from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config_loader import load_config, parse_remote_dirs


class ConfigValidationTests(unittest.TestCase):
    def _write(self, body: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "cfg.ini"
        path.write_text(body, encoding="utf-8")
        return path

    def test_invalid_protocol_raises(self):
        path = self._write(
            """
[notification]
mode = windows

[ftp_01]
host = example.com
username = u
protocol = sftp
remote_dirs = /in
""".strip()
        )
        with self.assertRaises(ValueError):
            load_config(path)

    def test_sample_values_are_warned_and_disabled(self):
        path = self._write(
            """
[notification]
mode = windows

[ftp_01]
enabled = true
display_name = Example FTPS
host = ftp.example.com
username = your_user
password = your_password
protocol = ftps-implicit
remote_dirs = /in
""".strip()
        )

        config = load_config(path)

        self.assertEqual(len(config.connections), 1)
        self.assertFalse(config.connections[0].enabled)
        self.assertTrue(any("ftp.example.com" in message for message in config.warnings))
        self.assertIn("Sample configuration detected. Skipping this connection.", config.warnings)

    def test_invalid_notification_mode_raises(self):
        path = self._write(
            """
[notification]
mode = invalid

[ftp_01]
host = example.com
username = u
protocol = ftp
remote_dirs = /in
""".strip()
        )
        with self.assertRaises(ValueError):
            load_config(path)

    def test_mail_settings_loaded_from_ini(self):
        path = self._write(
            """
[notification]
mode = mail

[mail]
enabled = true
provider = gmail
smtp_server = smtp.gmail.com
smtp_port = 587
use_tls = true
username = sender@gmail.com
password = app_password
from_address = sender@gmail.com
to_address = receiver@example.com
subject = [FTPWATCH] updated

[ftp_01]
host = real.example.local
username = u
password = p
protocol = ftp
remote_dirs = /in
""".strip()
        )

        config = load_config(path)

        self.assertEqual(config.notification.mode, "mail")
        self.assertTrue(config.mail.enabled)
        self.assertEqual(config.mail.username, "sender@gmail.com")
        self.assertEqual(config.mail.password, "app_password")
        self.assertEqual(config.mail.from_address, "sender@gmail.com")
        self.assertEqual(config.mail.to_address, "receiver@example.com")

    def test_general_new_poll_and_backoff_settings(self):
        path = self._write(
            """
[general]
poll_interval_seconds = 10
keep_connection_alive = true
backoff_enabled = true
backoff_schedule_seconds = 10,20,30,60

[notification]
mode = windows

[ftp_01]
host = real.example.local
username = u
password = p
protocol = ftp
remote_dirs = /in
""".strip()
        )

        config = load_config(path)
        self.assertEqual(config.general.poll_interval_seconds, 10)
        self.assertTrue(config.general.keep_connection_alive)
        self.assertTrue(config.general.backoff_enabled)
        self.assertEqual(config.general.backoff_schedule_seconds, [10, 20, 30, 60])

    def test_poll_seconds_is_backward_compatible(self):
        path = self._write(
            """
[general]
poll_seconds = 77

[notification]
mode = windows

[ftp_01]
host = real.example.local
username = u
password = p
protocol = ftp
remote_dirs = /in
""".strip()
        )

        config = load_config(path)
        self.assertEqual(config.general.poll_interval_seconds, 77)

    def test_parse_remote_dirs_pipe_delimited(self):
        self.assertEqual(parse_remote_dirs("/a|/b"), ["/a", "/b"])

    def test_parse_remote_dirs_trims_whitespace(self):
        self.assertEqual(parse_remote_dirs(" /a/ |  /b/ "), ["/a/", "/b/"])

    def test_parse_remote_dirs_ignores_trailing_pipe(self):
        self.assertEqual(parse_remote_dirs("/a|/b|"), ["/a", "/b"])

    def test_parse_remote_dirs_csv_backward_compatible(self):
        self.assertEqual(parse_remote_dirs("/upload,/upload/layout"), ["/upload", "/upload/layout"])

    def test_empty_remote_dirs_raises(self):
        path = self._write(
            """
[notification]
mode = windows

[ftp_01]
host = example.com
username = u
protocol = ftp
remote_dirs =   |   
""".strip()
        )
        with self.assertRaises(ValueError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
