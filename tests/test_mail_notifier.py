from __future__ import annotations

import smtplib
import unittest
from logging import getLogger
from unittest.mock import patch

from app.models import MailConfig
from app.notifier import MailNotifier
from mail import send_ftp_notice


class _FakeSMTP:
    def __init__(self, server: str, port: int, timeout: int):
        self.server = server
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None
        self.sent = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username: str, password: str):
        self.logged_in = (username, password)

    def send_message(self, _msg):
        self.sent = True


class MailTests(unittest.TestCase):
    def test_send_ftp_notice_uses_gmail_smtp(self):
        config = MailConfig(
            enabled=True,
            provider="gmail",
            smtp_server="smtp.gmail.com",
            smtp_port=587,
            use_tls=True,
            username="sender@gmail.com",
            password="app-pass",
            from_address="sender@gmail.com",
            to_address="receiver@example.com",
            subject="[FTPWATCH] updated",
        )

        created = {}

        def fake_smtp(server: str, port: int, timeout: int = 20):
            obj = _FakeSMTP(server, port, timeout)
            created["smtp"] = obj
            return obj

        with patch.object(smtplib, "SMTP", side_effect=fake_smtp):
            ok = send_ftp_notice({"path": "/a.txt"}, config)

        self.assertTrue(ok)
        smtp = created["smtp"]
        self.assertEqual(smtp.server, "smtp.gmail.com")
        self.assertEqual(smtp.port, 587)
        self.assertTrue(smtp.started_tls)
        self.assertEqual(smtp.logged_in, ("sender@gmail.com", "app-pass"))
        self.assertTrue(smtp.sent)

    def test_mail_notifier_send_update_success(self):
        config = MailConfig(enabled=True, smtp_server="smtp.gmail.com", smtp_port=587, from_address="a@a", to_address="b@b", password="x")
        notifier = MailNotifier(config, "mail.py", getLogger("test"))
        notifier._send_func = lambda payload, cfg: True

        ok = notifier.send_update({"path": "/a.txt", "size": 1})

        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
