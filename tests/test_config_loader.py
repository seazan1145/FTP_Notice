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


if __name__ == "__main__":
    unittest.main()
