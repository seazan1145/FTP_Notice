from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.main import _ensure_runtime_config


class MainConfigBootstrapTests(unittest.TestCase):
    def test_missing_ini_is_copied_from_sample_and_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            sample = work / "config" / "ftp_monitor.sample.ini"
            target = work / "config" / "ftp_monitor.ini"
            sample.parent.mkdir(parents=True, exist_ok=True)
            sample.write_text("[general]\npoll_seconds=60\n", encoding="utf-8")

            import app.main as main_module

            original_sample = main_module.DEFAULT_SAMPLE_CONFIG_PATH
            main_module.DEFAULT_SAMPLE_CONFIG_PATH = sample
            try:
                should_exit = _ensure_runtime_config(target)
            finally:
                main_module.DEFAULT_SAMPLE_CONFIG_PATH = original_sample

            self.assertTrue(should_exit)
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), sample.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
