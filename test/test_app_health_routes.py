from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import api.app as app_module
import api.system as system_module


class FakeStorage:
    def get_backend_info(self) -> dict[str, object]:
        return {"type": "json"}

    def health_check(self) -> dict[str, object]:
        return {"status": "healthy"}


class FakeConfig:
    app_version = "9.9.9-test"

    def cleanup_old_images(self) -> None:
        return None

    def get_storage_backend(self) -> FakeStorage:
        return FakeStorage()


class FakeProxySettings:
    def get_runtime_status(self) -> dict[str, object]:
        return {"enabled": False}


class FakeAccountService:
    def get_stats(self) -> dict[str, object]:
        return {
            "total": 1,
            "cumulative_total": 1,
            "active": 1,
            "unlimited_quota_count": 0,
            "total_quota": 1,
            "limited": 0,
            "abnormal": 0,
            "disabled": 0,
            "total_success": 0,
            "total_fail": 0,
            "by_type": {"web": 1},
        }


class AppHealthRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        patchers = [
            mock.patch.object(app_module, "config", FakeConfig()),
            mock.patch.object(app_module, "start_limited_account_watcher"),
            mock.patch.object(app_module, "start_image_cleanup_scheduler"),
            mock.patch.object(app_module, "backup_service"),
            mock.patch.object(system_module, "config", FakeConfig()),
            mock.patch.object(system_module, "proxy_settings", FakeProxySettings()),
            mock.patch("services.account_service.account_service", FakeAccountService()),
            mock.patch.object(app_module, "resolve_web_asset", return_value=Path(__file__)),
        ]
        for patcher in patchers:
            patched = patcher.start()
            if isinstance(getattr(patched, "return_value", None), mock.Mock):
                patched.return_value.join.return_value = None
            self.addCleanup(patcher.stop)
        self.client = TestClient(app_module.create_app())

    def test_root_head_supports_host_health_checks(self) -> None:
        response = self.client.head("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "")


if __name__ == "__main__":
    unittest.main()
