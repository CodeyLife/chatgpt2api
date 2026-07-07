from __future__ import annotations

import base64
import io
import unittest
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from services.protocol import conversation


def png_bytes(mode: str = "RGBA") -> bytes:
    buffer = io.BytesIO()
    image = Image.new(mode, (2, 2), (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0))
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ConversationImageFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved_payloads: list[bytes] = []
        self.old_config_data = dict(conversation.config.data)

        def fake_save(image_data: bytes, _base_url: str | None = None):
            self.saved_payloads.append(image_data)
            suffix = "jpg" if image_data.startswith(b"\xff\xd8") else "png"
            return SimpleNamespace(url=f"http://app.test/images/result.{suffix}")

        self.storage_patcher = mock.patch.object(conversation.image_storage_service, "save", side_effect=fake_save)
        self.storage_patcher.start()

    def tearDown(self) -> None:
        self.storage_patcher.stop()
        conversation.config.data.clear()
        conversation.config.data.update(self.old_config_data)

    def test_format_image_result_converts_b64_json_and_saved_bytes_to_jpeg(self) -> None:
        conversation.config.data["image_convert_result_to_jpg"] = True
        source = png_bytes("RGBA")

        result = conversation.format_image_result(
            [{"b64_json": base64.b64encode(source).decode("ascii")}],
            "red square",
            "b64_json",
            "http://app.test",
            123,
        )

        returned = base64.b64decode(result["data"][0]["b64_json"])
        self.assertEqual(returned, self.saved_payloads[0])
        self.assertEqual(result["data"][0]["url"], "http://app.test/images/result.jpg")
        with Image.open(io.BytesIO(returned)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.mode, "RGB")

    def test_format_image_result_preserves_original_png_when_conversion_disabled(self) -> None:
        conversation.config.data["image_convert_result_to_jpg"] = False
        source = png_bytes("RGB")

        result = conversation.format_image_result(
            [{"b64_json": base64.b64encode(source).decode("ascii")}],
            "red square",
            "b64_json",
            "http://app.test",
            123,
        )

        returned = base64.b64decode(result["data"][0]["b64_json"])
        self.assertEqual(returned, source)
        self.assertEqual(self.saved_payloads[0], source)
        self.assertEqual(result["data"][0]["url"], "http://app.test/images/result.png")


if __name__ == "__main__":
    unittest.main()
