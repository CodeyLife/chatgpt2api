import unittest

from services.register.account_diagnostics import (
    detect_account_unusable_payload,
    detect_account_unusable_response_body,
    detect_account_unusable_text,
)


class RegisterAccountDiagnosticsTests(unittest.TestCase):
    def test_detect_account_unusable_text_maps_markers_to_codes(self):
        self.assertEqual(detect_account_unusable_text("Your account has been deactivated."), "account_deactivated")
        self.assertEqual(detect_account_unusable_text("账号已删除"), "account_deleted")
        self.assertEqual(detect_account_unusable_text("account_banned"), "account_banned")
        self.assertEqual(detect_account_unusable_text("normal login page"), "")

    def test_detect_account_unusable_payload_uses_structured_error_code(self):
        self.assertEqual(
            detect_account_unusable_payload({"error": {"code": "account_deleted"}}),
            "account_deleted",
        )
        self.assertEqual(detect_account_unusable_payload({"error": {"code": "rate_limit"}}), "")

    def test_detect_account_unusable_response_body_ignores_plain_text(self):
        self.assertEqual(
            detect_account_unusable_response_body('{"error":{"code":"account_deactivated"}}'),
            "account_deactivated",
        )
        self.assertEqual(detect_account_unusable_response_body("Your account has been deactivated."), "")


if __name__ == "__main__":
    unittest.main()
