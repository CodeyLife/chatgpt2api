import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "analyze_har_protocol.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("analyze_har_protocol", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sentinel_p(array):
    raw = json.dumps(array, separators=(",", ":")).encode("utf-8")
    return "gAAAAAC" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class AnalyzeHarProtocolTests(unittest.TestCase):
    def test_analyze_har_payload_classifies_and_redacts_sensitive_values(self):
        module = load_script_module()
        p_token = sentinel_p(["1920x1080", "date", 1, 2, "ua", "https://sentinel.openai.com/sdk.js"])
        har = {
            "log": {
                "entries": [
                    {
                        "request": {
                            "method": "POST",
                            "url": "https://chatgpt.com/api/auth/signin/openai?code=secret-code&prompt=login",
                            "headers": [
                                {"name": "authorization", "value": "Bearer access-token"},
                                {"name": "x-test", "value": "ok"},
                            ],
                            "postData": {"text": json.dumps({"csrfToken": "csrf-1", "p": p_token, "regular": "value"})},
                        },
                        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
                    },
                    {
                        "request": {
                            "method": "GET",
                            "url": "https://auth.openai.com/api/accounts/authorize?state=state-1",
                            "headers": [
                                {
                                    "name": "openai-sentinel-token",
                                    "value": json.dumps({"flow": "authorize_continue", "p": p_token}),
                                }
                            ],
                        },
                        "response": {"status": 302, "content": {"mimeType": "text/html", "text": ""}},
                    },
                ]
            }
        }

        summary = module.analyze_har_payload(har, source="sample.har")

        self.assertEqual(summary["entry_count"], 2)
        self.assertEqual(summary["classes"]["nextauth-oauth"], 1)
        self.assertEqual(summary["classes"]["openai-auth"], 1)
        self.assertIn("code=%3Credacted%3Alen%3D11%3E", summary["requests"][0]["url"])
        self.assertEqual(summary["requests"][0]["request_headers"]["authorization"], "<redacted:len=19>")
        self.assertEqual(summary["requests"][0]["post"]["csrfToken"], "<redacted:len=6>")
        self.assertEqual(summary["requests"][0]["post"]["regular"], "value")
        self.assertEqual(len(summary["fingerprints"]), 2)
        self.assertEqual(summary["js_entrypoints"], ["https://sentinel.openai.com/sdk.js"])

    def test_cli_writes_summary_file(self):
        module = load_script_module()
        har = {"log": {"entries": [{"request": {"method": "GET", "url": "https://chatgpt.com/backend-api/me"}, "response": {"status": 200}}]}}
        with tempfile.TemporaryDirectory() as tmp:
            har_path = Path(tmp) / "sample.har"
            out_path = Path(tmp) / "summary.json"
            har_path.write_text(json.dumps(har), encoding="utf-8")

            exit_code = module.main([str(har_path), "-o", str(out_path)])

            self.assertEqual(exit_code, 0)
            data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(data["classes"]["chatgpt-auth-bootstrap"], 1)


if __name__ == "__main__":
    unittest.main()
