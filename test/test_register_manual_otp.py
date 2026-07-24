import threading
import time
import unittest

from services.register import mail_provider, manual_otp


class RegisterManualOTPTests(unittest.TestCase):
    def test_submit_and_wait_for_manual_otp(self):
        result = {}

        def waiter():
            result["code"] = manual_otp.wait_for_manual_otp("user@example.com", timeout=3)

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)

        waiting = manual_otp.list_waiting()
        self.assertEqual(waiting[0]["email"], "user@example.com")
        manual_otp.submit_manual_otp("user@example.com", "123456")
        thread.join(timeout=2)

        self.assertEqual(result["code"], "123456")
        self.assertEqual(manual_otp.list_waiting(), [])

    def test_manual_mail_provider_uses_configured_mailbox_and_pre_submitted_code(self):
        config = {
            "wait_timeout": 3,
            "wait_interval": 0.1,
            "providers": [
                {
                    "type": "manual",
                    "enable": True,
                    "mailboxes": "manual@example.com",
                }
            ],
        }
        mailbox = mail_provider.create_mailbox(config)
        manual_otp.submit_manual_otp("manual@example.com", "654321")

        self.assertEqual(mailbox["address"], "manual@example.com")
        self.assertEqual(mail_provider.wait_for_code(config, mailbox), "654321")

    def test_manual_otp_rejects_bad_code(self):
        with self.assertRaises(ValueError):
            manual_otp.submit_manual_otp("user@example.com", "abc")


if __name__ == "__main__":
    unittest.main()
