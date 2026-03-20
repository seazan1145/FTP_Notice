from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config_loader import load_config


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
[general]
poll_seconds = 10
stable_seconds = 30
connect_timeout = 10
read_timeout = 10

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
[general]
poll_seconds = 10
stable_seconds = 30
connect_timeout = 10
read_timeout = 10

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
[general]
notification_mode = invalid

[ftp_01]
host = example.com
username = u
protocol = ftp
remote_dirs = /in
""".strip()
        )
        with self.assertRaises(ValueError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
