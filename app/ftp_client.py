from __future__ import annotations

import ftplib
import socket
from collections.abc import Iterable

from .models import FtpConnectionConfig, GeneralConfig, RemoteFileInfo


class FtpClient:
    def __init__(self, config: FtpConnectionConfig, general: GeneralConfig) -> None:
        self.config = config
        self.general = general
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
        try:
            entries = list(self.ftp.mlsd(current_dir))
            for name, facts in entries:
                if name in {".", ".."}:
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
            return
        except (ftplib.error_perm, AttributeError):
            pass

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
        try:
            for name, facts in self.ftp.mlsd(target_dir):
                if facts.get("type") != "file":
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
        except (ftplib.error_perm, AttributeError):
            pass

        for row in self._list_via_list(target_dir):
            name, size, is_dir = row
            if is_dir:
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
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                continue
            perms = parts[0]
            is_dir = perms.startswith("d")
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            name = parts[8]
            rows.append((name, size, is_dir))
        return rows


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
