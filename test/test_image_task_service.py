from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from services.image_task_service import ImageTaskService


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}
ONE_BY_ONE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00"
    b"\x1b\xb6\xeeV\x00\x00\x00\x00IEND\xaeB`\x82"
)


def wait_for_task(service: ImageTaskService, identity: dict[str, object], task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = service.list_tasks(identity, [task_id])
        last = (result.get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class ImageTaskServiceTests(unittest.TestCase):
    def make_service(self, path: Path, handler=None) -> ImageTaskService:
        return ImageTaskService(
            path,
            generation_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/image.png"}]}),
            edit_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]}),
            retention_days_getter=lambda: 30,
        )

    def test_duplicate_submit_uses_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            first = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            second = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(first["id"], "task-1")
            self.assertEqual(second["id"], "task-1")
            task = wait_for_task(service, OWNER, "task-1", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/image.png")
            self.assertEqual(calls, 1)

    def test_different_owner_cannot_query_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="private-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            wait_for_task(service, OWNER, "private-task", "success")
            result = service.list_tasks(OTHER_OWNER, ["private-task"])

            self.assertEqual(result["items"], [])
            self.assertEqual(result["missing_ids"], ["private-task"])

    def test_streaming_task_exposes_partial_data_while_running(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            first_emitted = False

            def handler(_payload):
                nonlocal first_emitted
                yield {
                    "object": "image.generation.result",
                    "index": 1,
                    "total": 2,
                    "data": [{"url": "http://example.test/one.png"}],
                }
                first_emitted = True
                time.sleep(0.15)
                yield {
                    "object": "image.generation.result",
                    "index": 2,
                    "total": 2,
                    "data": [{"url": "http://example.test/two.png"}],
                }

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="partial-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
                n=2,
            )

            deadline = time.time() + 1.0
            partial = None
            while time.time() < deadline:
                item = service.list_tasks(OWNER, ["partial-task"])["items"][0]
                if item.get("status") == "running" and len(item.get("data") or []) == 1:
                    partial = item
                    break
                time.sleep(0.01)

            self.assertTrue(first_emitted)
            self.assertIsNotNone(partial)
            self.assertEqual(partial["data"][0]["index"], 1)
            self.assertEqual(partial["completed_count"], 1)
            self.assertEqual(partial["failed_indices"], [2])
            final = wait_for_task(service, OWNER, "partial-task", "success")
            self.assertEqual([item["index"] for item in final["data"]], [1, 2])
            self.assertEqual(final["completed_count"], 2)
            self.assertEqual(final["failed_indices"], [])

    def test_streaming_task_keeps_partial_success_after_late_failure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            def handler(_payload):
                yield {
                    "object": "image.generation.result",
                    "index": 1,
                    "total": 2,
                    "data": [{"url": "http://example.test/one.png"}],
                }
                raise RuntimeError("late failure")

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="partial-fail-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
                n=2,
            )

            task = wait_for_task(service, OWNER, "partial-fail-task", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/one.png")
            self.assertEqual(task["completed_count"], 1)
            self.assertEqual(task["failed_indices"], [2])
            self.assertIn("late failure", task["error"])

    def test_streaming_task_uses_message_when_no_images_returned(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            def handler(_payload):
                yield {
                    "object": "image.generation.message",
                    "message": "upstream policy blocked this request",
                }

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="message-only-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
                n=2,
            )

            task = wait_for_task(service, OWNER, "message-only-task", "error")
            self.assertEqual(task["data"], [])
            self.assertEqual(task["completed_count"], 0)
            self.assertEqual(task["failed_indices"], [1, 2])
            self.assertIn("upstream policy blocked", task["error"])

    def test_streaming_generation_usage_includes_prompt_tokens(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            def handler(_payload):
                yield {
                    "object": "image.generation.result",
                    "index": 1,
                    "total": 1,
                    "data": [{"url": "http://example.test/one.png"}],
                }

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_generation(
                OWNER,
                client_task_id="usage-generation-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            task = wait_for_task(service, OWNER, "usage-generation-task", "success")
            details = task["usage"]["input_tokens_details"]
            self.assertGreater(details["text_tokens"], 0)
            self.assertEqual(details["image_tokens"], 0)

    def test_streaming_edit_usage_includes_input_image_tokens(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            def handler(_payload):
                yield {
                    "object": "image.generation.result",
                    "index": 1,
                    "total": 1,
                    "data": [{"url": "http://example.test/one.png"}],
                }

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            service.submit_edit(
                OWNER,
                client_task_id="usage-edit-task",
                prompt="edit",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
                images=[(ONE_BY_ONE_PNG, "one.png", "image/png")],
            )

            task = wait_for_task(service, OWNER, "usage-edit-task", "success")
            details = task["usage"]["input_tokens_details"]
            self.assertGreater(details["text_tokens"], 0)
            self.assertGreater(details["image_tokens"], 0)

    def test_success_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            service.submit_generation(
                OWNER,
                client_task_id="persisted-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "persisted-task", "success")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persisted-task"])

            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["status"], "success")
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/image.png")

    def test_startup_marks_unfinished_tasks_as_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "queued-task",
                                "owner_id": "owner-1",
                                "status": "queued",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["queued-task", "running-task"])

            self.assertEqual([item["status"] for item in result["items"]], ["error", "error"])
            self.assertTrue(all("已中断" in item.get("error", "") for item in result["items"]))


if __name__ == "__main__":
    unittest.main()
