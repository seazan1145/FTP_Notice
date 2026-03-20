from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage

from app.models import MailConfig


logger = logging.getLogger(__name__)


def send_ftp_notice(data: dict, config: MailConfig) -> bool:
    username = config.username or config.from_address
    if not config.enabled:
        logger.error("Mail sending is disabled: mail.enabled=false")
        return False
    if not config.smtp_server or not config.from_address or not config.to_address:
        logger.error("Mail configuration incomplete: smtp_server/from_address/to_address are required")
        return False

    msg = EmailMessage()
    msg["Subject"] = config.subject
    msg["From"] = config.from_address
    msg["To"] = config.to_address
    msg.set_content(json.dumps(data, ensure_ascii=False), subtype="plain", charset="utf-8")

    try:
        with smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=20) as server:
            if config.use_tls:
                server.starttls()
            if username and config.password:
                server.login(username, config.password)
            server.send_message(msg)
        logger.info("Mail transport send success: smtp=%s:%s to=%s", config.smtp_server, config.smtp_port, config.to_address)
        return True
    except Exception:
        logger.exception("Mail transport send failed: smtp=%s:%s to=%s", config.smtp_server, config.smtp_port, config.to_address)
        return False
