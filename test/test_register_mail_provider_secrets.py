from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.register_service import RegisterService


class RegisterMailProviderSecretsTests(unittest.TestCase):
    def test_qqmail_imap_password_is_redacted_and_preserved_on_blank_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            service.update(
                {
                    "mail": {
                        "request_timeout": 30,
                        "wait_timeout": 30,
                        "wait_interval": 2,
                        "api_use_register_proxy": True,
                        "providers": [
                            {
                                "enable": True,
                                "type": "qqmail_imap",
                                "domain": ["example.com"],
                                "qq_email": "receiver@qq.com",
                                "imap_password": "secret-auth-code",
                                "imap_host": "imap.qq.com",
                            }
                        ],
                    }
                }
            )

            public_provider = service.get()["mail"]["providers"][0]
            self.assertEqual(public_provider["imap_password"], "")
            self.assertTrue(public_provider["has_imap_password"])

            service.update(
                {
                    "mail": {
                        "providers": [
                            {
                                **public_provider,
                                "imap_password": "",
                                "has_imap_password": True,
                                "qq_email": "receiver2@qq.com",
                            }
                        ]
                    }
                }
            )

            self.assertEqual(service._config["mail"]["providers"][0]["imap_password"], "secret-auth-code")
            self.assertEqual(service._config["mail"]["providers"][0]["qq_email"], "receiver2@qq.com")


if __name__ == "__main__":
    unittest.main()
