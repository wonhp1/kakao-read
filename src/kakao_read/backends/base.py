"""플랫폼마다 다른 '카카오톡 창 읽기·보내기'를 봇이 한 모양으로 쓰도록 맞춘다.

새 플랫폼은 Backend 의 세 메서드만 채우면 된다. 봇은 그 밖의 것을 모른다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BackendError(Exception):
    """아무것도 보내지 않았다 (보내기 전 단계에서 멈춤). 다음 차례에 다시 해 볼 수 있다."""


class SendUncertain(Exception):
    """전송을 누른 뒤 확인하지 못했다 — 나갔을 수 있다. 중복을 막으려고 다시 보내지 않는다."""


class BackendUnavailable(Exception):
    """이 컴퓨터에서는 아직 쓸 수 없는 백엔드."""


@dataclass(frozen=True)
class ChatMessage:
    text: str
    sender: str | None      # 받은 메시지의 보낸 사람 (이어 보낸 줄은 앞 사람 이름을 물려받는다)
    outgoing: bool          # 이 컴퓨터의 카카오톡 계정이 보낸 메시지 (봇의 답 포함)
    system: bool = False    # 입퇴장·공지 같은 시스템 줄
    thread_comment: bool = False  # 스레드 댓글을 '채팅방에 함께 보내기'로 방에 올린 글
    time: str | None = None


@dataclass(frozen=True)
class Snapshot:
    messages: list[ChatMessage]   # 오래된 것부터
    scrolled_up: bool = False     # 방 창이 위로 스크롤돼 있어 최근 메시지가 아닐 수 있음


class Backend(Protocol):
    name: str

    def windows(self) -> list[tuple[str, str]]:
        """열린 카카오톡 창 (제목, 상태)."""

    def read(self, room: str, count: int) -> Snapshot:
        """방 창 맨 아래 글 메시지 count 개."""

    def send(self, room: str, text: str) -> str:
        """일반 메시지 하나를 보내고 확인 결과를 돌려준다. BackendError·SendUncertain 을 낸다."""

    # 선택: history(room, max_messages, progress=None) -> list[ChatMessage]
    #   방을 위로 올려 지난 대화까지 읽는다 (오래된 것부터). 없는 백엔드는 read() 로 보이는 만큼만 쓴다.
