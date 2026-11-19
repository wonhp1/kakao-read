"""새로 올라온 메시지 가려내기.

화면에서 읽은 메시지에는 번호가 없다. 지난번에 읽은 목록의 끝부분이 이번 목록의 앞부분과 겹치는 만큼을
찾아, 그 뒤를 새 메시지로 본다. 시각 라벨은 같은 사람 묶음의 마지막 줄로 옮겨 다니고, 맨 위 줄의 이름표는
창 밖으로 밀려나면 사라지므로 비교에 쓰지 않는다 (본문·보낸 쪽·시스템 줄만).

처음 읽을 때와 겹치는 부분을 못 찾을 때(한꺼번에 너무 많이 왔거나 글이 지워짐)는 답하지 않고 지금 화면을
기준으로 다시 잡는다 — 옛 메시지에 답하는 것보다 하나 놓치는 편이 낫다.
"""
from __future__ import annotations

from kakao_read.backends.base import ChatMessage


def _key(m: ChatMessage) -> tuple:
    return (m.text, m.outgoing, m.system)


def overlap(prev: list[tuple], cur: list[tuple]) -> int:
    """prev 끝 k 개 == cur 앞 k 개 인 가장 큰 k. 없으면 0."""
    for k in range(min(len(prev), len(cur)), 0, -1):
        if prev[-k:] == cur[:k]:
            return k
    return 0


class RoomTracker:
    def __init__(self) -> None:
        self._seen: list[tuple] | None = None

    @property
    def ready(self) -> bool:
        return self._seen is not None

    def update(self, messages: list[ChatMessage]) -> list[ChatMessage] | None:
        """새 메시지 목록. 처음 읽었거나 흐름을 놓쳤으면 None (기준만 다시 잡는다)."""
        keys = [_key(m) for m in messages]
        prev, self._seen = self._seen, keys
        if prev is None:
            return None
        if not keys:
            return []
        if not prev:
            return list(messages)          # 빈 방에 처음 올라온 글
        k = overlap(prev, keys)
        if k == 0:
            return None
        return list(messages[k:])
