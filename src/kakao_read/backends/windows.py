"""윈도우 — UI Automation(UIA)으로 읽고 보낼 자리.

윈도우 카카오톡이 UIA 로 메시지 글을 내주는지 아직 확인하지 못했다. probe/kakao_win_probe.py 로
확인한 결과(probe-report.json)를 보고 read·send 를 채운다. 그 전까지는 봇이 이 백엔드로 돌지 않는다.
"""
from __future__ import annotations

from kakao_read.backends.base import BackendUnavailable, Snapshot

PROBE_GUIDE = (
    "윈도우 카카오톡 읽기·보내기는 아직 준비 중입니다. 먼저 확인용 스크립트를 돌려 주세요:\n"
    "  uv run probe/kakao_win_probe.py windows\n"
    '  uv run probe/kakao_win_probe.py probe "방 이름" --expect "화면에 보이는 메시지 일부"\n'
    "나온 probe-report.json 을 보고 이 백엔드를 채웁니다 (README 의 '윈도우' 절)."
)


class WindowsBackend:
    name = "Windows UI Automation (준비 중)"

    def windows(self) -> list[tuple[str, str]]:
        raise BackendUnavailable(PROBE_GUIDE)

    def read(self, room: str, count: int) -> Snapshot:
        raise BackendUnavailable(PROBE_GUIDE)

    def send(self, room: str, text: str) -> str:
        raise BackendUnavailable(PROBE_GUIDE)
