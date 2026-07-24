from __future__ import annotations

import unittest
from datetime import datetime, timezone

from services.register import mail_provider


class FakeQQMailProvider(mail_provider.QQMailIMAPProvider):
    def __init__(self, entry: dict, conf: dict, messages: list[dict]):
        super().__init__(entry, conf)
        self.messages = messages

    def fetch_recent_messages(self, mailbox: dict) -> list[dict]:
        return list(self.messages)


class QQMailProviderTests(unittest.TestCase):
    def test_create_mailbox_generates_domain_address(self) -> None:
        provider = mail_provider.QQMailIMAPProvider(
            {
                "provider_ref": "qqmail_imap#1",
                "domain": ["example.com"],
                "qq_email": "receiver@qq.com",
                "imap_password": "auth-code",
            },
            {"request_timeout": 1, "wait_timeout": 1, "wait_interval": 0.2, "user_agent": "test", "proxy": ""},
        )

        mailbox = provider.create_mailbox("alice")

        self.assertEqual(mailbox["provider"], "qqmail_imap")
        self.assertEqual(mailbox["address"], "alice@example.com")
        self.assertEqual(mailbox["qq_email"], "receiver@qq.com")

    def test_wait_for_code_filters_target_to_address(self) -> None:
        provider = FakeQQMailProvider(
            {"domain": ["example.com"], "qq_email": "receiver@qq.com", "imap_password": "auth-code"},
            {"request_timeout": 1, "wait_timeout": 1, "wait_interval": 0.2, "user_agent": "test", "proxy": ""},
            [
                {
                    "provider": "qqmail_imap",
                    "mailbox": "other@example.com",
                    "message_id": "1",
                    "to": "other@example.com",
                    "subject": "Your OpenAI code is 111111",
                    "received_at": datetime.now(timezone.utc),
                },
                {
                    "provider": "qqmail_imap",
                    "mailbox": "target@example.com",
                    "message_id": "2",
                    "to": "target@example.com",
                    "subject": "OpenAI",
                    "text_content": "Your verification code is 222222.",
                    "received_at": datetime.now(timezone.utc),
                },
            ],
        )

        code = provider.wait_for_code({"address": "target@example.com"})

        self.assertEqual(code, "222222")

    def test_create_provider_accepts_cloudflare_domain_alias(self) -> None:
        provider = mail_provider._create_provider(
            {
                "request_timeout": 1,
                "wait_timeout": 1,
                "wait_interval": 0.2,
                "user_agent": "test",
                "providers": [
                    {
                        "enable": True,
                        "type": "cloudflare_domain",
                        "domain": ["example.com"],
                        "qq_email": "receiver@qq.com",
                        "imap_password": "auth-code",
                    }
                ],
            }
        )

        self.assertIsInstance(provider, mail_provider.QQMailIMAPProvider)


if __name__ == "__main__":
    unittest.main()
