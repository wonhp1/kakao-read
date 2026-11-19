from __future__ import annotations

import sys

from kakao_read.backends.base import (
    Backend,
    BackendError,
    BackendUnavailable,
    ChatMessage,
    SendUncertain,
    Snapshot,
)

__all__ = ["Backend", "BackendError", "BackendUnavailable", "ChatMessage", "SendUncertain", "Snapshot",
           "get_backend"]


def get_backend() -> Backend:
    if sys.platform == "darwin":
        from kakao_read.backends.mac import MacBackend
        return MacBackend()
    if sys.platform == "win32":
        from kakao_read.backends.windows import WindowsBackend
        return WindowsBackend()
    raise BackendUnavailable(f"지원하지 않는 운영체제입니다: {sys.platform} (macOS·Windows 만)")
