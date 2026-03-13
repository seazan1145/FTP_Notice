from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from .config_loader import DEFAULT_CONFIG_PATH, DEFAULT_SAMPLE_CONFIG_PATH, load_config
from .db import MonitorDatabase
from .logger_setup import setup_logger
from .monitor import MonitorService
from .notifier import WindowsNotifier
from .utils import ensure_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FTP Monitor Notifier")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to INI config")
    parser.add_argument("--once", action="store_true", help="Run one scan cycle and exit")
    parser.add_argument("--test-notify", action="store_true", help="Send a test notification and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging for one-shot FTPS diagnostics")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if _ensure_runtime_config(args.config):
        return 1

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"Failed to load config: {exc}")
        return 1

    ensure_dir(config.root_dir / "data")
    ensure_dir(config.root_dir / "logs")

    log_level = "DEBUG" if args.debug else config.general.log_level
    logger = setup_logger(config.root_dir / "logs", log_level)
    logger.info("Application started.")
    if args.debug:
        logger.debug("Debug mode enabled (--debug).")

    for warning in config.warnings:
        logger.warning(warning)

    db = MonitorDatabase(config.db_path)
    db.initialize()

    notifier = WindowsNotifier(logger)
    if not notifier.available:
        logger.warning("Desktop notifications are currently disabled (backend=%s).", notifier.backend_name)

    if args.test_notify:
        ok = notifier.send_windows_notification("FTP新着ファイル", "[Test]\n/test\nexample.txt")
        if ok:
            logger.info("Test notification sent successfully.")
            db.close()
            return 0
        logger.error("Test notification failed. Ensure notification dependency is installed and backend is available.")
        db.close()
        return 1

    service = MonitorService(config, db, notifier, logger)

    try:
        while True:
            service.run_once()
            logger.info("Scan completed.")
            if args.once:
                break
            time.sleep(config.general.poll_seconds)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
    except Exception:
        logger.exception("Unhandled error in main loop")
        return 1
    finally:
        db.close()
        logger.info("Application stopped.")

    return 0


def _ensure_runtime_config(config_path: Path) -> bool:
    if config_path.exists():
        return False

    sample_path = DEFAULT_SAMPLE_CONFIG_PATH
    if not sample_path.exists():
        print(f"Failed to initialize config: sample file not found: {sample_path}")
        return True

    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(sample_path, config_path)
    print(f"Created config file from sample: {config_path}")
    print("Please edit config/ftp_monitor.ini and replace all sample values before rerunning.")
    print("Monitoring was not started.")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
