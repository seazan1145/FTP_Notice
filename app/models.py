from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class GeneralConfig:
    poll_seconds: int = 60
    stable_seconds: int = 30
    connect_timeout: int = 15
    read_timeout: int = 30
    passive_mode: bool = True
    log_level: str = "INFO"


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
