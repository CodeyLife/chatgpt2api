from __future__ import annotations

import unittest

from services.register import mail_provider


class RegisterMailOTPExtractionTests(unittest.TestCase):
    def test_extract_code_prefers_single_subject_code(self) -> None:
        code = mail_provider._extract_code({"subject": "Your OpenAI code is 525210"})

        self.assertEqual(code, "525210")

    def test_extract_code_prefers_contextual_body_code_when_multiple_numbers_exist(self) -> None:
        code = mail_provider._extract_code({
            "subject": "Welcome",
            "text_content": "Ticket 123456 is not relevant. Your verification code is 654321.",
        })

        self.assertEqual(code, "654321")

    def test_extract_code_handles_html_and_ignores_known_template_number(self) -> None:
        code = mail_provider._extract_code({
            "html_content": """
                <style>.x{color:#123456}</style>
                <p>177010</p>
                <p>Your verification code</p>
                <p><strong>789012</strong></p>
            """,
        })

        self.assertEqual(code, "789012")

    def test_extract_code_handles_japanese_and_korean_context(self) -> None:
        japanese = mail_provider._extract_code({"text_content": "認証コード: 246810"})
        korean = mail_provider._extract_code({"text_content": "확인 코드 135791"})

        self.assertEqual(japanese, "246810")
        self.assertEqual(korean, "135791")

    def test_extract_code_reads_common_raw_provider_fields(self) -> None:
        code = mail_provider._extract_code({
            "raw": {
                "body": {
                    "content": "<div>验证码为 <b>112233</b></div>",
                }
            }
        })

        self.assertEqual(code, "112233")


if __name__ == "__main__":
    unittest.main()
