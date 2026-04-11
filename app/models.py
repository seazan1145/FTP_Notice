from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class NotificationConfig:
    mode: str = "windows"


@dataclass(slots=True)
class MailConfig:
    enabled: bool = False
    provider: str = "gmail"
    smtp_server: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    from_address: str = ""
    to_address: str = ""
    subject: str = "[FTPWATCH] updated"


@dataclass(slots=True)
class StartupConfig:
    notify_existing_on_start: bool = False


@dataclass(slots=True)
class GeneralConfig:
    poll_seconds: int = 60
    poll_interval_seconds: int = 60
    stable_seconds: int = 30
    connect_timeout: int = 15
    read_timeout: int = 30
    passive_mode: bool = True
    reconnect_on_error: bool = True
    keep_connection_alive: bool = True
    backoff_enabled: bool = True
    backoff_schedule_seconds: list[int] = field(default_factory=lambda: [10, 20, 30, 60])
    log_level: str = "INFO"
    mail_module_path: str = "mail.py"


@dataclass(slots=True)
class FtpConnectionConfig:
    section_name: str
    enabled: bool
    display_name: str
    protocol: str
    host: str
    port: int
    username: str
    password: str
    remote_dirs: list[str] = field(default_factory=list)
    recursive: bool = False
    include_extensions: list[str] = field(default_factory=list)
    exclude_extensions: list[str] = field(default_factory=list)
    exclude_name_contains: list[str] = field(default_factory=list)
    encoding: str = "utf-8"


@dataclass(slots=True)
class AppConfig:
    general: GeneralConfig
    notification: NotificationConfig
    mail: MailConfig
    startup: StartupConfig
    connections: list[FtpConnectionConfig]
    root_dir: Path
    db_path: Path
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RemoteFileInfo:
    connection_name: str
    remote_dir: str
    remote_path: str
    file_name: str
    file_size: int
    modified_at: Optional[str] = None
