import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.log_service import LoggedCall, LogService


class LogServiceResponsePreviewTests(unittest.TestCase):
    def _capture_call_log(self, call: LoggedCall, suffix: str, result=None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            local_service = LogService(Path(tmp) / "logs.jsonl")
            with mock.patch("services.log_service.log_service", local_service):
                call.log(suffix, result)
                return local_service.list(type="call", limit=1)[0]

    def test_chat_completion_log_includes_response_preview(self) -> None:
        call = LoggedCall(
            {"id": "key-1", "name": "test-key", "role": "admin"},
            "/v1/chat/completions",
            "auto",
            "文本生成",
            request_text="say hello",
        )
        item = self._capture_call_log(
            call,
            "调用完成",
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello from the model."}},
                ],
            },
        )

        self.assertEqual(item["detail"]["response_preview"], "Hello from the model.")
        self.assertEqual(item["detail"]["response_text_length"], 21)

    def test_empty_stream_log_records_zero_response_length(self) -> None:
        call = LoggedCall(
            {"id": "key-1", "name": "test-key", "role": "admin"},
            "/v1/responses",
            "auto",
            "Responses",
            request_text="say nothing",
        )
        item = self._capture_call_log(
            call,
            "流式调用结束",
            [
                {"type": "response.output_text.done", "text": ""},
                {"type": "response.completed", "response": {"output": []}},
            ],
        )

        self.assertNotIn("response_preview", item["detail"])
        self.assertEqual(item["detail"]["response_text_length"], 0)

    def test_stream_collects_response_preview_from_chunks(self) -> None:
        call = LoggedCall(
            {"id": "key-1", "name": "test-key", "role": "admin"},
            "/v1/chat/completions",
            "auto",
            "文本生成",
            request_text="say hello",
        )
        chunks = iter(
            [
                {"choices": [{"delta": {"role": "assistant", "content": "Hello "}, "finish_reason": None}]},
                {"choices": [{"delta": {"content": "stream."}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            local_service = LogService(Path(tmp) / "logs.jsonl")
            with mock.patch("services.log_service.log_service", local_service):
                self.assertEqual(list(call.stream(chunks))[-1]["choices"][0]["finish_reason"], "stop")
                item = local_service.list(type="call", limit=1)[0]

        self.assertEqual(item["detail"]["response_preview"], "Hello stream.")
        self.assertEqual(item["detail"]["response_text_length"], 13)


if __name__ == "__main__":
    unittest.main()
