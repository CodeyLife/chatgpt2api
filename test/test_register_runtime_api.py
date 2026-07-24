import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.register import create_router


class RegisterRuntimeAPITests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(create_router())
        self.client = TestClient(app)

    def test_runtime_endpoint_requires_admin_and_returns_status(self):
        fake_register = {
            "registration_driver": "browser_use",
            "drivers": [{"name": "browser_use", "label": "Browser Use"}],
            "sentinel_browser_chrome_path": "",
        }
        fake_runtime = {
            "playwright": {"available": True, "version": "1.49.0", "error": ""},
            "sentinel": {"available": False, "chrome_path": "", "error": "missing chrome"},
        }

        with (
            patch("api.register.require_admin", return_value={"role": "admin"}) as require_admin,
            patch("api.register.register_service.get", return_value=fake_register),
            patch("api.register.browser_automation_status", return_value=fake_runtime),
        ):
            response = self.client.get("/api/register/runtime", headers={"Authorization": "Bearer test"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime"], fake_runtime)
        self.assertEqual(response.json()["registration_driver"], "browser_use")
        self.assertEqual(response.json()["drivers"][0]["name"], "browser_use")
        require_admin.assert_called_once_with("Bearer test")


if __name__ == "__main__":
    unittest.main()
