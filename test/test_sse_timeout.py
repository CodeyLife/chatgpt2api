import time

import pytest

from services.protocol import conversation as conversation_protocol
from utils.helper import SSEStreamTimeoutError, iter_sse_payloads


class _NormalExitResponse:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def iter_lines(self):
        while not self.closed:
            time.sleep(0.01)
        return
        yield b""


class _RaisingExitResponse:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def iter_lines(self):
        while not self.closed:
            time.sleep(0.01)
        raise RuntimeError("socket closed")
        yield b""


@pytest.mark.parametrize("response_cls", [_NormalExitResponse, _RaisingExitResponse])
def test_iter_sse_payloads_raises_when_watchdog_closes_stream(response_cls):
    response = response_cls()

    with pytest.raises(SSEStreamTimeoutError, match="read timed out"):
        list(iter_sse_payloads(response, stream_timeout_secs=0.05))

    assert response.closed


class _CaptureConversationBackend:
    def __init__(self) -> None:
        self.timeout_secs = None

    def stream_conversation(self, **kwargs):
        self.timeout_secs = kwargs.get("timeout_secs")
        return iter(["[DONE]"])


def test_image_conversation_stream_timeout_uses_remaining_total_deadline(monkeypatch):
    monkeypatch.setitem(conversation_protocol.config.data, "image_total_timeout_secs", 600)
    backend = _CaptureConversationBackend()

    list(conversation_protocol.conversation_events(
        backend,
        model="gpt-image-2",
        prompt="draw a city poster",
        deadline=time.time() + 600,
    ))

    assert backend.timeout_secs is not None
    assert backend.timeout_secs > 300
