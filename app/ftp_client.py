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


class FtpDataConnectionTlsError(RuntimeError):
    """Raised when FTPS data channel TLS/session negotiation fails."""


class FtpConnectTimeoutError(TimeoutError):
    """Raised when control-channel connect/login times out with troubleshooting hints."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        protocol: str,
        timeout_seconds: int,
        phase: str,
        original_exc: Exception,
    ) -> None:
        hint = _build_connect_timeout_hint(protocol=protocol, port=port)
        message = (
            f"Timed out during FTP {phase} after {timeout_seconds}s "
            f"(host={host}, port={port}, protocol={protocol}). {hint}"
        )
        super().__init__(message)
        self.host = host
        self.port = port
        self.protocol = protocol
        self.timeout_seconds = timeout_seconds
        self.phase = phase
        self.original_exc = original_exc


def _build_connect_timeout_hint(protocol: str, port: int) -> str:
    if protocol == "ftps-implicit":
        return (
            "確認ポイント: サーバーが Implicit FTPS を許可しているか、"
            "ポート 990/TCP が許可されているか、ファイアウォール/VPN で遮断されていないかを確認してください。"
        )
    if protocol == "ftps-explicit":
        if port != 21:
            return (
                "確認ポイント: Explicit FTPS は通常 21 番ポートです。"
                "接続先ポートとサーバー設定が一致しているか確認してください。"
            )
        return (
            "確認ポイント: サーバーが Explicit FTPS(FTPES) を許可しているか、"
            "21/TCP が許可されているか確認してください。"
        )
    if protocol == "ftp" and port == 990:
        return (
            "確認ポイント: 990 番は一般的に Implicit FTPS 用です。"
            "FTP 平文で接続していないか、protocol 設定を確認してください。"
        )
    return "確認ポイント: ホスト名、ポート、プロトコル、ネットワーク経路を確認してください。"


class FtpClient:
    def __init__(self, config: FtpConnectionConfig, general: GeneralConfig, logger: logging.Logger | None = None) -> None:
        self.config = config
        self.general = general
        self.logger = logger or logging.getLogger(__name__)
        self.ftp: ftplib.FTP | ftplib.FTP_TLS | None = None

    @property
    def is_connected(self) -> bool:
        return self.ftp is not None

    def connect(self) -> None:
        mode = self._connection_mode()
        if mode == "ftps-implicit":
            ftp = _ImplicitFTP_TLS(timeout=self.general.connect_timeout)
        elif mode == "ftps-explicit":
            ftp = _ExplicitFTP_TLS(timeout=self.general.connect_timeout)
        else:
            ftp = ftplib.FTP(timeout=self.general.connect_timeout)

        self.logger.debug("Connecting with mode=%s host=%s port=%s", mode, self.config.host, self.config.port)
        try:
            ftp.connect(self.config.host, self.config.port)
        except TimeoutError as exc:
            raise FtpConnectTimeoutError(
                host=self.config.host,
                port=self.config.port,
                protocol=mode,
                timeout_seconds=self.general.connect_timeout,
                phase="connect",
                original_exc=exc,
            ) from exc

        try:
            ftp.login(self.config.username, self.config.password)
        except TimeoutError as exc:
            raise FtpConnectTimeoutError(
                host=self.config.host,
                port=self.config.port,
                protocol=mode,
                timeout_seconds=self.general.connect_timeout,
                phase="login",
                original_exc=exc,
            ) from exc
        ftp.set_pasv(self.general.passive_mode)
        ftp.encoding = self.config.encoding
        ftp.timeout = self.general.read_timeout

        self.logger.debug("Connection established: mode=%s passive_mode=%s", mode, self.general.passive_mode)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
            self.logger.debug("prot_p() executed for mode=%s", mode)
        else:
            self.logger.debug("prot_p() skipped for mode=%s", mode)
        self.ftp = ftp

    def maybe_noop(self) -> bool:
        if self.ftp is None:
            return False
        try:
            self.ftp.voidcmd("NOOP")
            return True
        except Exception:
            try:
                self.ftp.pwd()
                return True
            except Exception:
                return False

    def ensure_connected(self) -> None:
        if self.ftp is None:
            self.connect()
            return
        if self.maybe_noop():
            return
        self.logger.warning("Connection health check failed. Reconnecting: %s", self.config.display_name)
        self.disconnect()
        self.connect()

    def _connection_mode(self) -> str:
        return self.config.protocol.lower().strip()

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
        success, entries, mlsd_tls_issue = self._try_mlsd(current_dir)
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
                        entry_type="file",
                    )
                else:
                    self.logger.debug("Skipping MLSD entry with unsupported type: path=%s type=%s", path, item_type)
            return

        self.logger.warning("MLSD failed for %s. Falling back to LIST.", current_dir)
        if mlsd_tls_issue:
            self.logger.warning("MLSD failed due to FTPS data-channel TLS/session error: dir=%s", current_dir)
        try:
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
                        entry_type="file",
                    )
        except FtpDataConnectionTlsError:
            if mlsd_tls_issue:
                self.logger.error(
                    "MLSD and LIST both failed. FTPS data connection issue is likely (not a LIST parse problem): dir=%s",
                    current_dir,
                )
            raise

    def _list_single_dir(self, root_dir: str, target_dir: str) -> Iterable[RemoteFileInfo]:
        assert self.ftp is not None
        success, entries, mlsd_tls_issue = self._try_mlsd(target_dir)
        if success:
            self.logger.debug("MLSD succeeded: dir=%s entries=%s", target_dir, len(entries))
            for name, facts in entries:
                item_type = facts.get("type", "")
                path = f"{target_dir.rstrip('/')}/{name}" if target_dir != "/" else f"/{name}"
                if item_type not in {"file", "dir"}:
                    self.logger.debug("Skipping MLSD unsupported entry: dir=%s name=%s type=%s", target_dir, name, item_type)
                    continue
                yield RemoteFileInfo(
                    connection_name=self.config.display_name,
                    remote_dir=root_dir,
                    remote_path=path,
                    file_name=name,
                    file_size=int(facts.get("size", 0)) if item_type == "file" else 0,
                    modified_at=facts.get("modify"),
                    entry_type="folder" if item_type == "dir" else "file",
                )
            return

        self.logger.warning("MLSD failed for %s. Falling back to LIST.", target_dir)
        if mlsd_tls_issue:
            self.logger.warning("MLSD failed due to FTPS data-channel TLS/session error: dir=%s", target_dir)
        try:
            for row in self._list_via_list(target_dir):
                name, size, is_dir = row
                path = f"{target_dir.rstrip('/')}/{name}" if target_dir != "/" else f"/{name}"
                yield RemoteFileInfo(
                    connection_name=self.config.display_name,
                    remote_dir=root_dir,
                    remote_path=path,
                    file_name=name,
                    file_size=0 if is_dir else size,
                    entry_type="folder" if is_dir else "file",
                )
        except FtpDataConnectionTlsError:
            if mlsd_tls_issue:
                self.logger.error(
                    "MLSD and LIST both failed. FTPS data connection issue is likely (not a LIST parse problem): dir=%s",
                    target_dir,
                )
            raise

    def _list_via_list(self, target_dir: str) -> list[tuple[str, int, bool]]:
        assert self.ftp is not None
        lines: list[str] = []
        self.logger.debug("LIST start: dir=%s", target_dir)
        try:
            self.ftp.retrlines(f"LIST {target_dir}", lines.append)
        except Exception as exc:
            self._log_data_connection_error("LIST", target_dir, exc)
            if self._is_ftps_data_tls_issue(exc):
                self.logger.error("Server may require TLS session reuse on data connections: dir=%s", target_dir)
                raise FtpDataConnectionTlsError(str(exc)) from exc
            raise
        self.logger.debug("LIST end: dir=%s lines=%s", target_dir, len(lines))

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

    def _try_mlsd(self, target_dir: str) -> tuple[bool, list[tuple[str, dict[str, str]]], bool]:
        assert self.ftp is not None
        self.logger.debug("MLSD start: dir=%s", target_dir)
        try:
            entries = list(self.ftp.mlsd(target_dir))
            self.logger.debug("MLSD end: dir=%s entries=%s", target_dir, len(entries))
            return (True, entries, False)
        except Exception as exc:
            self._log_data_connection_error("MLSD", target_dir, exc)
            return (False, [], self._is_ftps_data_tls_issue(exc))

    def _is_ftps_data_tls_issue(self, exc: Exception) -> bool:
        if isinstance(exc, ssl.SSLEOFError):
            return True
        if isinstance(exc, ftplib.error_temp):
            text = str(exc).lower()
            return "tls session of data connection not resumed" in text or ("425" in text and "not resumed" in text)
        return False

    def _log_data_connection_error(self, op: str, target_dir: str, exc: Exception) -> None:
        if isinstance(exc, ssl.SSLEOFError):
            self.logger.warning("%s failed due to FTPS data-channel TLS/session error: dir=%s error=%s", op, target_dir, exc)
            return
        if isinstance(exc, ftplib.error_temp) and self._is_ftps_data_tls_issue(exc):
            self.logger.warning(
                "%s failed due to FTPS data-channel TLS/session reuse requirement: dir=%s error=%s",
                op,
                target_dir,
                exc,
            )
            return
        if isinstance(exc, ftplib.error_perm):
            self.logger.info("%s unavailable due to permission/server response: dir=%s error=%s", op, target_dir, exc)
            return
        if isinstance(exc, OSError):
            self.logger.info("%s failed due to network/system OSError: dir=%s error=%s", op, target_dir, exc)
            return
        self.logger.info("%s unavailable for %s: %s", op, target_dir, exc)


class _BaseReusableTLSFTP(ftplib.FTP_TLS):
    """FTP_TLS that attempts TLS session reuse for data-channel sockets."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if not self._prot_p:
            return conn, size

        logger = logging.getLogger("ftp_monitor")
        host = getattr(self, "host", None)
        session = getattr(self.sock, "session", None)

        if session is not None:
            try:
                conn = self.context.wrap_socket(conn, server_hostname=host, session=session)
                logger.debug("FTPS data connection wrapped with TLS session reuse.")
                return conn, size
            except TypeError:
                logger.debug("TLS session kwarg unsupported; falling back to normal data-channel wrap.")
            except Exception as exc:
                logger.warning("FTPS data-channel wrap with session reuse failed; retrying without session: %s", exc)

        try:
            conn = self.context.wrap_socket(conn, server_hostname=host)
            logger.debug("FTPS data connection wrapped without session reuse.")
            return conn, size
        except Exception as exc:
            logger.exception("Failed to establish FTPS data connection TLS wrap.")
            raise FtpDataConnectionTlsError("FTPS data connection TLS wrap failed") from exc


class _ExplicitFTP_TLS(_BaseReusableTLSFTP):
    """Explicit FTPS with data-channel TLS session reuse support."""


class _ImplicitFTP_TLS(_BaseReusableTLSFTP):
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
