from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")

from services.account_service import AccountService
from services.auth_service import AuthService
from services.config import config
from services.openai_backend_api import InvalidAccessTokenError
from services.register import openai_register
from services.storage.json_storage import JSONStorageBackend
from utils.helper import anonymize_token, split_image_model


class AccountCapabilityTests(unittest.TestCase):
    def test_image_accounts_require_positive_quota(self) -> None:
        self.assertFalse(
            AccountService._is_image_account_available(
                {"status": "限流", "quota": 1}
            )
        )
        self.assertFalse(
            AccountService._is_image_account_available(
                {"status": "正常", "quota": 0}
            )
        )
        self.assertTrue(AccountService._is_image_account_available({"status": "正常", "quota": 1}))

    def test_prolite_variants_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertEqual(service._normalize_account_type("prolite"), "ProLite")
            self.assertEqual(service._normalize_account_type("pro_lite"), "ProLite")

    def test_search_account_type_ignores_unrelated_scalar_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            self.assertIsNone(
                service._search_account_type(
                    {
                        "amr": ["pwd", "otp", "mfa"],
                        "chatgpt_compute_residency": "no_constraint",
                        "chatgpt_data_residency": "no_constraint",
                        "user_id": "user-I52GFfLGFM0dokFk2dBiKEBn",
                    }
                )
            )

    def test_mark_image_result_consumes_quota(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_accounts(["token-1"])
            service.update_account(
                "token-1",
                {
                    "status": "正常",
                    "quota": 1,
                },
            )

            updated = service.mark_image_result("token-1", success=True)

            self.assertIsNotNone(updated)
            self.assertEqual(updated["quota"], 0)
            self.assertEqual(updated["status"], "限流")

    def test_split_image_model_supports_plan_type_prefix(self) -> None:
        self.assertEqual(split_image_model("gpt-image-2"), (None, "gpt-image-2"))
        self.assertEqual(split_image_model("plus-codex-gpt-image-2"), ("plus", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("team-codex-gpt-image-2"), ("team", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("pro-codex-gpt-image-2"), ("pro", "codex-gpt-image-2"))
        self.assertEqual(split_image_model("plus-gpt-image-2"), (None, None))
        self.assertEqual(split_image_model("unknown-image-model"), (None, None))

    def test_get_available_access_token_filters_by_plan_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            service.add_account_items(
                [
                    {"access_token": "token-plus", "type": "Plus", "status": "正常", "quota": 3},
                    {"access_token": "token-pro", "type": "Pro", "status": "正常", "quota": 3},
                ]
            )

            service.fetch_remote_info = lambda access_token, event="fetch_remote_info": service.get_account(access_token)

            plus_token = service.get_available_access_token(plan_type="plus")
            pro_token = service.get_available_access_token(plan_type="pro")
            service.release_image_slot(plus_token)
            service.release_image_slot(pro_token)

            self.assertEqual(plus_token, "token-plus")
            self.assertEqual(pro_token, "token-pro")

    def test_new_account_warmup_blocks_image_scheduling_until_verified_and_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            now = datetime.now(timezone.utc)
            service.add_account_items(
                [
                    {
                        "access_token": "warm-token",
                        "status": "正常",
                        "quota": 3,
                        "warmup_until": (now + timedelta(minutes=30)).isoformat(),
                        "next_health_check_at": now.isoformat(),
                        "health_score": 0,
                        "last_health_event": "registered",
                    },
                    {"access_token": "ready-token", "status": "正常", "quota": 3},
                ]
            )
            service.fetch_remote_info = lambda access_token, event="fetch_remote_info": service.get_account(access_token)

            token = service.get_available_access_token()
            service.release_image_slot(token)

            self.assertEqual(token, "ready-token")
            self.assertEqual(service.list_new_account_health_tokens(), ["warm-token"])

            service.update_account(
                "warm-token",
                {
                    "first_verified_at": now.isoformat(),
                    "warmup_until": (now - timedelta(seconds=1)).isoformat(),
                    "health_score": 2,
                },
            )
            service.update_account("ready-token", {"status": "限流", "quota": 0})
            warm_token = service.get_available_access_token()
            service.release_image_slot(warm_token)
            self.assertEqual(warm_token, "warm-token")

    def test_new_account_warmup_blocks_text_scheduling_until_verified_and_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            now = datetime.now(timezone.utc)
            service.add_account_items(
                [
                    {
                        "access_token": "warm-text-token",
                        "status": "正常",
                        "warmup_until": (now + timedelta(minutes=30)).isoformat(),
                        "next_health_check_at": now.isoformat(),
                        "health_score": 0,
                        "last_health_event": "registered",
                    },
                    {"access_token": "ready-text-token", "status": "正常"},
                ]
            )

            self.assertEqual(service.get_text_access_token(), "ready-text-token")

            service.update_account(
                "warm-text-token",
                {
                    "first_verified_at": now.isoformat(),
                    "warmup_until": (now - timedelta(seconds=1)).isoformat(),
                    "health_score": 2,
                },
            )
            service.update_account("ready-text-token", {"status": "异常"})

            self.assertEqual(service.get_text_access_token(), "warm-text-token")

    def test_new_account_invalid_token_is_not_removed_during_warmup(self) -> None:
        original_value = config.data.get("auto_remove_invalid_accounts")
        config.data["auto_remove_invalid_accounts"] = True
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                now = datetime.now(timezone.utc)
                service.add_account_items(
                    [
                        {
                            "access_token": "new-invalid-token",
                            "status": "正常",
                            "warmup_until": (now + timedelta(minutes=30)).isoformat(),
                            "next_health_check_at": now.isoformat(),
                            "health_score": 0,
                            "last_health_event": "registered",
                        }
                    ]
                )

                with patch(
                    "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                    side_effect=InvalidAccessTokenError("token invalidated (/backend-api/me)"),
                ):
                    result = service.refresh_accounts(["new-invalid-token"], defer_invalid_removal=False)

                account = service.get_account("new-invalid-token")
                self.assertEqual(result["refreshed"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertIsNotNone(account)
                self.assertEqual(account["status"], "正常")
                self.assertEqual(account["invalid_count"], 1)
                self.assertEqual(account["last_health_event"], "invalid_access_token")
                self.assertLess(account["health_score"], 0)
                self.assertTrue(account["next_health_check_at"])
        finally:
            if original_value is None:
                config.data.pop("auto_remove_invalid_accounts", None)
            else:
                config.data["auto_remove_invalid_accounts"] = original_value

    def test_unverified_new_account_can_be_marked_invalid_after_warmup_confirmation(self) -> None:
        original_value = config.data.get("auto_remove_invalid_accounts")
        config.data["auto_remove_invalid_accounts"] = False
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                now = datetime.now(timezone.utc)
                service.add_account_items(
                    [
                        {
                            "access_token": "expired-warmup-invalid-token",
                            "status": "正常",
                            "created_at": (now - timedelta(hours=1)).isoformat(),
                            "warmup_until": (now - timedelta(minutes=30)).isoformat(),
                            "first_verified_at": None,
                            "next_health_check_at": now.isoformat(),
                            "invalid_count": 2,
                            "last_invalid_at": (now - timedelta(minutes=1)).isoformat(),
                            "health_score": -4,
                            "last_health_event": "invalid_access_token",
                        }
                    ]
                )

                with patch(
                    "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                    side_effect=InvalidAccessTokenError("token invalidated (/backend-api/me)"),
                ):
                    result = service.refresh_accounts(["expired-warmup-invalid-token"])

                account = service.get_account("expired-warmup-invalid-token")
                self.assertEqual(result["refreshed"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertIsNotNone(account)
                self.assertEqual(account["status"], "异常")
                self.assertEqual(account["invalid_count"], 3)
                self.assertEqual(account["last_health_event"], "invalid_access_token")
        finally:
            if original_value is None:
                config.data.pop("auto_remove_invalid_accounts", None)
            else:
                config.data["auto_remove_invalid_accounts"] = original_value

    def test_new_account_successful_health_check_records_first_verified_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
            now = datetime.now(timezone.utc)
            service.add_account_items(
                [
                    {
                        "access_token": "new-good-token",
                        "status": "正常",
                        "quota": 0,
                        "warmup_until": (now + timedelta(minutes=30)).isoformat(),
                        "next_health_check_at": now.isoformat(),
                        "health_score": 0,
                        "last_health_event": "registered",
                    }
                ]
            )

            with patch(
                "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                return_value={"status": "正常", "quota": 5, "type": "free"},
            ):
                result = service.verify_new_accounts(["new-good-token"])

            account = service.get_account("new-good-token")
            self.assertEqual(result["refreshed"], 1)
            self.assertIsNotNone(account)
            self.assertTrue(account["first_verified_at"])
            self.assertEqual(account["last_health_event"], "health_check_success")
            self.assertGreaterEqual(account["health_score"], 2)
            self.assertIsNone(account["next_health_check_at"])

    def test_new_account_health_queue_respects_worker_limit(self) -> None:
        with patch.object(openai_register, "config", {**openai_register.config, "new_account_max_verify_workers": 2}):
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                now = datetime.now(timezone.utc)
                service.add_account_items(
                    [
                        {
                            "access_token": f"new-token-{index}",
                            "status": "正常",
                            "warmup_until": (now + timedelta(minutes=30)).isoformat(),
                            "next_health_check_at": now.isoformat(),
                        }
                        for index in range(4)
                    ]
                )

                self.assertEqual(len(service.list_new_account_health_tokens()), 2)

    def test_refresh_accounts_can_remove_invalid_token_without_confirmation_delay(self) -> None:
        original_value = config.data.get("auto_remove_invalid_accounts")
        config.data["auto_remove_invalid_accounts"] = True
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                service.add_account_items([{"access_token": "invalid-token", "status": "正常"}])

                with patch(
                    "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                    side_effect=InvalidAccessTokenError("token invalidated (/backend-api/me)"),
                ):
                    result = service.refresh_accounts(["invalid-token"], defer_invalid_removal=False)

                self.assertEqual(result["refreshed"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertEqual(result["items"], [])
                self.assertIsNone(service.get_account("invalid-token"))
        finally:
            if original_value is None:
                config.data.pop("auto_remove_invalid_accounts", None)
            else:
                config.data["auto_remove_invalid_accounts"] = original_value

    def test_refresh_accounts_defers_invalid_token_removal_by_default(self) -> None:
        original_value = config.data.get("auto_remove_invalid_accounts")
        config.data["auto_remove_invalid_accounts"] = True
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                service = AccountService(JSONStorageBackend(Path(tmp_dir) / "accounts.json"))
                service.add_account_items([{"access_token": "invalid-token", "status": "正常"}])

                with patch(
                    "services.openai_backend_api.OpenAIBackendAPI.get_user_info",
                    side_effect=InvalidAccessTokenError("token invalidated (/backend-api/me)"),
                ):
                    result = service.refresh_accounts(["invalid-token"])

                account = service.get_account("invalid-token")
                self.assertEqual(result["refreshed"], 0)
                self.assertEqual(len(result["errors"]), 1)
                self.assertIsNotNone(account)
                self.assertEqual(account["invalid_count"], 1)
        finally:
            if original_value is None:
                config.data.pop("auto_remove_invalid_accounts", None)
            else:
                config.data["auto_remove_invalid_accounts"] = original_value


class TokenLogTests(unittest.TestCase):
    def test_anonymize_token_hides_raw_value(self) -> None:
        token = "super-secret-token"
        token_ref = anonymize_token(token)

        self.assertTrue(token_ref.startswith("token:"))
        self.assertNotIn(token, token_ref)


class AuthServiceTests(unittest.TestCase):
    def test_create_authenticate_disable_and_delete_user_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))

            item, raw_key = service.create_key(role="user", name="Alice")

            self.assertEqual(item["role"], "user")
            self.assertEqual(item["name"], "Alice")
            self.assertTrue(item["enabled"])
            self.assertTrue(raw_key.startswith("sk-"))

            authed = service.authenticate(raw_key)
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertEqual(authed["role"], "user")
            self.assertIsNotNone(authed["last_used_at"])

            updated = service.update_key(item["id"], {"enabled": False}, role="user")
            self.assertIsNotNone(updated)
            self.assertFalse(updated["enabled"])
            self.assertIsNone(service.authenticate(raw_key))

            self.assertTrue(service.delete_key(item["id"], role="user"))
            self.assertFalse(service.delete_key(item["id"], role="user"))
            self.assertEqual(service.list_keys(role="user"), [])

    def test_authenticate_ignores_last_used_save_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            def fail_save() -> None:
                raise OSError("disk unavailable")

            service._save = fail_save

            authed = service.authenticate(raw_key)

            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])
            self.assertIsNotNone(authed["last_used_at"])

    def test_update_user_key_replaces_raw_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            item, raw_key = service.create_key(role="user", name="Alice")

            updated = service.update_key(item["id"], {"key": "sk-user-custom-key"}, role="user")

            self.assertIsNotNone(updated)
            self.assertIsNone(service.authenticate(raw_key))

            authed = service.authenticate("sk-user-custom-key")
            self.assertIsNotNone(authed)
            self.assertEqual(authed["id"], item["id"])

    def test_user_key_name_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = AuthService(JSONStorageBackend(Path(tmp_dir) / "accounts.json", Path(tmp_dir) / "auth_keys.json"))
            first, _ = service.create_key(role="user", name="Alice")
            second, _ = service.create_key(role="user", name="Bob")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.create_key(role="user", name="Alice")

            with self.assertRaisesRegex(ValueError, "这个名称已经在使用中了"):
                service.update_key(second["id"], {"name": "Alice"}, role="user")

            updated = service.update_key(first["id"], {"name": "Alice"}, role="user")
            self.assertIsNotNone(updated)
            self.assertEqual(updated["name"], "Alice")


if __name__ == "__main__":
    unittest.main()
