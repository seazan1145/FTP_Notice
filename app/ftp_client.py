from __future__ import annotations

import ftplib
import logging
import re
import socket
import ssl
from collections.abc import Iterable

from .models import FtpConnectionConfig, GeneralConfig, RemoteFileInfo


_UNIX_LIST_PATTERN = re.compile(
    r"^(?P<perms>[\-dlpscbD][rwxStTs\-]{9})\s+"
    r"(?P<links>\d+)\s+"
    r"(?P<owner>\S+)\s+"
    r"(?P<group>\S+)\s+"
    r"(?P<size>\d+)\s+"
    r"(?P<month>[A-Za-z]{3})\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<time_year>\d{2}:\d{2}|\d{4})\s+"
    r"(?P<name>.+)$"
)

_WINDOWS_LIST_PATTERN = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{2,4})\s+"
    r"(?P<time>\d{2}:\d{2}(?:AM|PM))\s+"
    r"(?P<size_or_dir><DIR>|\d+)\s+"
    r"(?P<name>.+)$",
    re.IGNORECASE,
)


class FtpClient:
    def __init__(self, config: FtpConnectionConfig, general: GeneralConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.general = general
        self.logger = logger or logging.getLogger(__name__)
        self.ftp: ftplib.FTP | ftplib.FTP_TLS | None = None

    def connect(self) -> None:
        if self._use_implicit_ftps():
            ftp = _ImplicitFTP_TLS(timeout=self.general.connect_timeout)
        elif self.config.protocol == "ftps":
            ftp = ftplib.FTP_TLS(timeout=self.general.connect_timeout)
        else:
            ftp = ftplib.FTP(timeout=self.general.connect_timeout)

        ftp.connect(self.config.host, self.config.port)
        ftp.login(self.config.username, self.config.password)
        ftp.set_pasv(self.general.passive_mode)
        ftp.encoding = self.config.encoding
        ftp.timeout = self.general.read_timeout

        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        self.ftp = ftp

    def _use_implicit_ftps(self) -> bool:
        protocol = self.config.protocol.lower()
        if protocol in {"ftps-implicit", "ftpsi", "implicit-ftps"}:
            return True
        return protocol == "ftps" and self.config.port == 990

    def disconnect(self) -> None:
        if self.ftp is None:
            return
        try:
            self.ftp.quit()
        except Exception:
            self.ftp.close()
        finally:
            self.ftp = None

    def list_files(self, remote_dir: str, recursive: bool = False) -> list[RemoteFileInfo]:
        if self.ftp is None:
            raise RuntimeError("FTP client is not connected")

        if recursive:
            return list(self._walk_recursive(remote_dir, remote_dir))
        return list(self._list_single_dir(remote_dir, remote_dir))

    def _walk_recursive(self, root_dir: str, current_dir: str) -> Iterable[RemoteFileInfo]:
        assert self.ftp is not None
        success, entries = self._try_mlsd(current_dir)
        if success:
            self.logger.debug("MLSD succeeded: dir=%s entries=%s", current_dir, len(entries))
            for name, facts in entries:
                if name in {".", ".."}:
                    self.logger.debug("Skipping special entry: %s/%s", current_dir, name)
                    continue
                item_type = facts.get("type", "")
                path = f"{current_dir.rstrip('/')}/{name}" if current_dir != "/" else f"/{name}"
                if item_type == "dir":
                    yield from self._walk_recursive(root_dir, path)
                elif item_type == "file":
                    size = int(facts.get("size", 0))
                    yield RemoteFileInfo(
                        connection_name=self.config.display_name,
                        remote_dir=root_dir,
                        remote_path=path,
                        file_name=name,
                        file_size=size,
                        modified_at=facts.get("modify"),
                    )
                else:
                    self.logger.debug("Skipping MLSD entry with unsupported type: path=%s type=%s", path, item_type)
            return

        self.logger.warning("MLSD failed for %s. Falling back to LIST.", current_dir)
        for row in self._list_via_list(current_dir):
            name, size, is_dir = row
            path = f"{current_dir.rstrip('/')}/{name}" if current_dir != "/" else f"/{name}"
            if is_dir:
                yield from self._walk_recursive(root_dir, path)
            else:
                yield RemoteFileInfo(
                    connection_name=self.config.display_name,
                    remote_dir=root_dir,
                    remote_path=path,
                    file_name=name,
                    file_size=size,
                )

    def _list_single_dir(self, root_dir: str, target_dir: str) -> Iterable[RemoteFileInfo]:
        assert self.ftp is not None
        success, entries = self._try_mlsd(target_dir)
        if success:
            self.logger.debug("MLSD succeeded: dir=%s entries=%s", target_dir, len(entries))
            for name, facts in entries:
                if facts.get("type") != "file":
                    self.logger.debug("Skipping MLSD non-file entry: dir=%s name=%s type=%s", target_dir, name, facts.get("type"))
                    continue
                path = f"{target_dir.rstrip('/')}/{name}" if target_dir != "/" else f"/{name}"
                yield RemoteFileInfo(
                    connection_name=self.config.display_name,
                    remote_dir=root_dir,
                    remote_path=path,
                    file_name=name,
                    file_size=int(facts.get("size", 0)),
                    modified_at=facts.get("modify"),
                )
            return

        self.logger.warning("MLSD failed for %s. Falling back to LIST.", target_dir)
        for row in self._list_via_list(target_dir):
            name, size, is_dir = row
            if is_dir:
                self.logger.debug("Skipping LIST directory entry in single-dir mode: %s/%s", target_dir, name)
                continue
            path = f"{target_dir.rstrip('/')}/{name}" if target_dir != "/" else f"/{name}"
            yield RemoteFileInfo(
                connection_name=self.config.display_name,
                remote_dir=root_dir,
                remote_path=path,
                file_name=name,
                file_size=size,
            )

    def _list_via_list(self, target_dir: str) -> list[tuple[str, int, bool]]:
        assert self.ftp is not None
        lines: list[str] = []
        self.ftp.retrlines(f"LIST {target_dir}", lines.append)
        rows: list[tuple[str, int, bool]] = []
        for line in lines:
            parsed = self._parse_list_line(line)
            if parsed is None:
                self.logger.warning("Skipping unparsable LIST line: %s", line)
                continue
            rows.append(parsed)
        self.logger.info("LIST completed: dir=%s entries=%s", target_dir, len(rows))
        return rows

    def _parse_list_line(self, line: str) -> tuple[str, int, bool] | None:
        unix = _UNIX_LIST_PATTERN.match(line)
        if unix:
            perms = unix.group("perms")
            is_dir = perms.startswith("d")
            size = int(unix.group("size"))
            name = unix.group("name")
            return (name, size, is_dir)

        windows = _WINDOWS_LIST_PATTERN.match(line)
        if windows:
            raw = windows.group("size_or_dir")
            is_dir = raw.upper() == "<DIR>"
            size = 0 if is_dir else int(raw)
            name = windows.group("name")
            return (name, size, is_dir)

        return None

    def _try_mlsd(self, target_dir: str) -> tuple[bool, list[tuple[str, dict[str, str]]]]:
        assert self.ftp is not None
        try:
            entries = list(self.ftp.mlsd(target_dir))
            return (True, entries)
        except (ssl.SSLEOFError, ftplib.error_perm, AttributeError, OSError) as exc:
            self.logger.info("MLSD unavailable for %s: %s", target_dir, exc)
            return (False, [])


class _ImplicitFTP_TLS(ftplib.FTP_TLS):
    """FTP over SSL/TLS from the first packet (implicit FTPS)."""

    def connect(self, host: str = "", port: int = 0, timeout: float = -999, source_address=None):
        if host:
            self.host = host
        if port > 0:
            self.port = port
        if timeout != -999:
            self.timeout = timeout

        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            source_address=source_address,
        )
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome
