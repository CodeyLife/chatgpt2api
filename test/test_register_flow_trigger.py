from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.register as register_api
from services.register import flow_trigger
from services.register_service import RegisterService


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "data": data, "timeout": timeout, "kwargs": self.kwargs})
        return FakeResponse(payload={"flow": {"flow_id": "flow-1"}, "message": "accepted"})

    def close(self):
        self.closed = True


class FakeProxySettings:
    def build_session_kwargs(self, **kwargs):
        return {"proxy": kwargs.get("proxy"), "verify": kwargs.get("verify")}


class RegisterFlowTriggerTests(unittest.TestCase):
    def setUp(self):
        FakeSession.calls = []

    def test_disabled_flow_trigger_is_skipped(self):
        result = flow_trigger.trigger_flow("access-token", {"flow_trigger": {"enabled": False}})

        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["ok"])

    def test_trigger_flow_posts_configured_payload_and_headers(self):
        config = {
            "proxy": "http://proxy.example:8080",
            "flow_trigger": {
                "enabled": True,
                "url": "https://flow.example/run",
                "bearer": "secret",
                "cookie": "sid=1",
                "payload": {"mode": "register"},
                "access_token_key": "token",
                "timeout": 12,
                "origin": "https://console.example",
                "referer": "https://console.example/",
                "use_register_proxy": True,
                "verify_ssl": False,
            },
        }

        with patch.object(flow_trigger, "proxy_settings", FakeProxySettings()):
            result = flow_trigger.trigger_flow("access-token", config, session_factory=FakeSession)

        self.assertTrue(result["ok"])
        self.assertEqual(result["flow_id"], "flow-1")
        call = FakeSession.calls[0]
        self.assertEqual(call["url"], "https://flow.example/run")
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(call["headers"]["Cookie"], "sid=1")
        self.assertEqual(call["headers"]["Origin"], "https://console.example")
        self.assertEqual(call["timeout"], 12)
        self.assertEqual(call["kwargs"], {"proxy": "http://proxy.example:8080", "verify": False})
        self.assertEqual(json.loads(call["data"]), {"mode": "register", "token": "access-token"})

    def test_trigger_flow_redacts_token_from_error_message(self):
        class ErrorSession(FakeSession):
            def post(self, *args, **kwargs):
                return FakeResponse(status_code=500, text="failed for access-token")

        config = {"flow_trigger": {"enabled": True, "url": "https://flow.example/run"}}

        with patch.object(flow_trigger, "proxy_settings", FakeProxySettings()):
            result = flow_trigger.trigger_flow("access-token", config, session_factory=ErrorSession)

        self.assertEqual(result["status"], "failed")
        self.assertNotIn("access-token", result["message"])
        self.assertIn("token:", result["message"])

    def test_register_api_accepts_flow_trigger_config(self):
        with TemporaryDirectory() as tmp:
            service = RegisterService(Path(tmp) / "register.json")
            app = FastAPI()
            app.include_router(register_api.create_router())

            with (
                patch.object(register_api, "register_service", service),
                patch.object(register_api, "require_admin", return_value={"role": "admin"}),
            ):
                client = TestClient(app)
                response = client.post(
                    "/api/register",
                    headers={"Authorization": "Bearer admin"},
                    json={
                        "flow_trigger": {
                            "enabled": True,
                            "url": "https://flow.example/run",
                            "payload": "{\"mode\":\"register\"}",
                            "timeout": "bad",
                        }
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        flow = response.json()["register"]["flow_trigger"]
        self.assertTrue(flow["enabled"])
        self.assertEqual(flow["url"], "https://flow.example/run")
        self.assertEqual(flow["payload"], {"mode": "register"})
        self.assertEqual(flow["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
