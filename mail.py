from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage

_RUNTIME_CONFIG: dict[str, object] = {}


def configure_mail(config: dict) -> None:
    _RUNTIME_CONFIG.update(config)


def _pick_str(name: str, env_name: str = "", default: str = "") -> str:
    value = _RUNTIME_CONFIG.get(name, "")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if env_name:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
    return default


def _pick_bool(name: str, env_name: str = "", default: bool = False) -> bool:
    value = _RUNTIME_CONFIG.get(name)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if env_name:
        env_value = os.getenv(env_name, "").strip().lower()
        if env_value:
            return env_value in {"1", "true", "yes", "on"}
    return default


def _pick_int(name: str, env_name: str = "", default: int = 0) -> int:
    value = _RUNTIME_CONFIG.get(name)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        return int(value.strip())
    if env_name:
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return int(env_value)
    return default


def send_ftp_notice(data: dict) -> bool:
    enabled = _pick_bool("mail_enabled", "FTP_NOTICE_MAIL_ENABLED", False)
    if not enabled:
        raise RuntimeError("mail sending is disabled (mail_enabled=false)")

    smtp_server = _pick_str("mail_smtp_server", "FTP_NOTICE_MAIL_SMTP_SERVER")
    smtp_port = _pick_int("mail_smtp_port", "FTP_NOTICE_MAIL_SMTP_PORT", 587)
    from_address = _pick_str("mail_from_address", "FTP_NOTICE_MAIL_FROM")
    to_address = _pick_str("mail_to_address", "FTP_NOTICE_MAIL_TO")
    subject = _pick_str("mail_subject", "FTP_NOTICE_MAIL_SUBJECT", "[FTPWATCH] updated")
    use_tls = _pick_bool("mail_use_tls", "FTP_NOTICE_MAIL_USE_TLS", True)
    username = _pick_str("mail_username", "FTP_NOTICE_MAIL_USERNAME", from_address)
    password = _pick_str("mail_password", "FTP_NOTICE_MAIL_PASSWORD")

    if not smtp_server or not from_address or not to_address:
        raise ValueError("mail configuration incomplete: smtp_server/from/to are required")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = to_address
    msg.set_content(json.dumps(data, ensure_ascii=False), subtype="plain", charset="utf-8")

    with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as server:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(msg)

    return True
