from __future__ import annotations

import configparser
from pathlib import Path

from .models import AppConfig, FtpConnectionConfig, GeneralConfig
from .utils import parse_bool, parse_csv


DEFAULT_CONFIG_PATH = Path("config/ftp_monitor.ini")


def load_config(config_path: Path = DEFAULT_CONFIG_PATH, root_dir: Path | None = None) -> AppConfig:
    parser = configparser.ConfigParser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    parser.read(config_path, encoding="utf-8")

    general = _load_general(parser)
    connections = _load_connections(parser)

    base = root_dir or Path(__file__).resolve().parents[1]
    db_path = base / "data" / "monitor.db"
    return AppConfig(general=general, connections=connections, root_dir=base, db_path=db_path)


def _load_general(parser: configparser.ConfigParser) -> GeneralConfig:
    section = parser["general"] if parser.has_section("general") else {}
    return GeneralConfig(
        poll_seconds=int(section.get("poll_seconds", 60)),
        stable_seconds=int(section.get("stable_seconds", 30)),
        connect_timeout=int(section.get("connect_timeout", 15)),
        read_timeout=int(section.get("read_timeout", 30)),
        passive_mode=parse_bool(section.get("passive_mode", "true"), True),
        log_level=section.get("log_level", "INFO"),
    )


def _load_connections(parser: configparser.ConfigParser) -> list[FtpConnectionConfig]:
    connections: list[FtpConnectionConfig] = []
    for section_name in parser.sections():
        if not section_name.lower().startswith("ftp_"):
            continue
        section = parser[section_name]
        host = section.get("host", "").strip()
        username = section.get("username", "").strip()
        if not host or not username:
            continue

        connections.append(
            FtpConnectionConfig(
                section_name=section_name,
                enabled=parse_bool(section.get("enabled", "true"), True),
                display_name=section.get("display_name", section_name),
                protocol=section.get("protocol", "ftp").lower(),
                host=host,
                port=int(section.get("port", 21)),
                username=username,
                password=section.get("password", ""),
                remote_dirs=parse_csv(section.get("remote_dirs", "")),
                recursive=parse_bool(section.get("recursive", "false"), False),
                include_extensions=[v.lower().lstrip(".") for v in parse_csv(section.get("include_extensions", ""))],
                exclude_extensions=[v.lower().lstrip(".") for v in parse_csv(section.get("exclude_extensions", ""))],
                exclude_name_contains=parse_csv(section.get("exclude_name_contains", "")),
                encoding=section.get("encoding", "utf-8"),
                poll_seconds_override=(
                    int(section.get("poll_seconds")) if section.get("poll_seconds") else None
                ),
            )
        )
    return connections
