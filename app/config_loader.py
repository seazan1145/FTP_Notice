from __future__ import annotations

import configparser
from pathlib import Path

from .models import AppConfig, FtpConnectionConfig, GeneralConfig
from .utils import parse_bool, parse_csv


DEFAULT_CONFIG_PATH = Path("config/ftp_monitor.ini")
DEFAULT_SAMPLE_CONFIG_PATH = Path("config/ftp_monitor.sample.ini")
ALLOWED_PROTOCOLS = {"ftp", "ftps-explicit", "ftps-implicit"}


SAMPLE_VALUE_WARNINGS = {
    "ftp.example.com": "sample host 'ftp.example.com'",
    "example.com": "sample host 'example.com'",
    "your_host": "sample host 'your_host'",
    "your_user": "sample username 'your_user'",
    "your_username": "sample username 'your_username'",
    "your_password": "sample password 'your_password'",
    "changeme": "sample password 'CHANGEME'",
}
PROTOCOL_ALIASES = {
    "ftp": "ftp",
    "ftps": "ftps-explicit",
    "ftps-explicit": "ftps-explicit",
    "ftps-implicit": "ftps-implicit",
    "ftpsi": "ftps-implicit",
    "implicit-ftps": "ftps-implicit",
}


def normalize_protocol(raw_protocol: str) -> str:
    protocol = raw_protocol.lower().strip()
    normalized = PROTOCOL_ALIASES.get(protocol)
    if normalized is None:
        raise ValueError(
            f"Invalid protocol: '{raw_protocol}'. Allowed values: {', '.join(sorted(ALLOWED_PROTOCOLS))}"
        )
    return normalized


def load_config(config_path: Path = DEFAULT_CONFIG_PATH, root_dir: Path | None = None) -> AppConfig:
    parser = configparser.ConfigParser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    parser.read(config_path, encoding="utf-8")

    general = _load_general(parser)
    connections, warnings = _load_connections(parser)

    base = root_dir or Path(__file__).resolve().parents[1]
    db_path = base / "data" / "monitor.db"
    return AppConfig(general=general, connections=connections, root_dir=base, db_path=db_path, warnings=warnings)


def _parse_positive_int(raw: str, field_name: str, minimum: int = 1) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: must be an integer, got '{raw}'") from exc
    if value < minimum:
        raise ValueError(f"Invalid {field_name}: must be >= {minimum}, got {value}")
    return value


def _load_general(parser: configparser.ConfigParser) -> GeneralConfig:
    section = parser["general"] if parser.has_section("general") else {}
    return GeneralConfig(
        poll_seconds=_parse_positive_int(section.get("poll_seconds", "60"), "general.poll_seconds"),
        stable_seconds=_parse_positive_int(section.get("stable_seconds", "30"), "general.stable_seconds"),
        connect_timeout=_parse_positive_int(section.get("connect_timeout", "15"), "general.connect_timeout"),
        read_timeout=_parse_positive_int(section.get("read_timeout", "30"), "general.read_timeout"),
        passive_mode=parse_bool(section.get("passive_mode", "true"), True),
        log_level=section.get("log_level", "INFO"),
    )


def _load_connections(parser: configparser.ConfigParser) -> tuple[list[FtpConnectionConfig], list[str]]:
    connections: list[FtpConnectionConfig] = []
    warnings: list[str] = []
    for section_name in parser.sections():
        if not section_name.lower().startswith("ftp_"):
            continue
        section = parser[section_name]
        host = section.get("host", "").strip()
        username = section.get("username", "").strip()
        if not host or not username:
            raise ValueError(f"Invalid {section_name}: host and username are required")

        try:
            protocol = normalize_protocol(section.get("protocol", "ftp"))
        except ValueError as exc:
            raise ValueError(
                f"Invalid {section_name}.protocol: {exc}"
            ) from exc

        remote_dirs = parse_csv(section.get("remote_dirs", ""))
        if not remote_dirs:
            raise ValueError(f"Invalid {section_name}: remote_dirs must not be empty")

        port = _parse_positive_int(section.get("port", "21"), f"{section_name}.port")

        sample_reason = _detect_sample_setting(host, username, section.get("password", ""))
        enabled = parse_bool(section.get("enabled", "true"), True)
        display_name = section.get("display_name", section_name)
        if sample_reason and enabled:
            warnings.append(
                f"Connection '{display_name}' uses {sample_reason}. Please replace it with your real FTP server/credentials."
            )
            warnings.append("Sample configuration detected. Skipping this connection.")
            enabled = False

        connections.append(
            FtpConnectionConfig(
                section_name=section_name,
                enabled=enabled,
                display_name=display_name,
                protocol=protocol,
                host=host,
                port=port,
                username=username,
                password=section.get("password", ""),
                remote_dirs=remote_dirs,
                recursive=parse_bool(section.get("recursive", "false"), False),
                include_extensions=[v.lower().lstrip(".") for v in parse_csv(section.get("include_extensions", ""))],
                exclude_extensions=[v.lower().lstrip(".") for v in parse_csv(section.get("exclude_extensions", ""))],
                exclude_name_contains=parse_csv(section.get("exclude_name_contains", "")),
                encoding=section.get("encoding", "utf-8"),
            )
        )
    return connections, warnings


def _detect_sample_setting(host: str, username: str, password: str) -> str | None:
    for raw in (host, username, password):
        key = raw.strip().lower()
        if key in SAMPLE_VALUE_WARNINGS:
            return SAMPLE_VALUE_WARNINGS[key]
    return None
