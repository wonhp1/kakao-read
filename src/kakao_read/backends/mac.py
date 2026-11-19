"""macOS — 접근성(AX)으로 읽고 보낸다. 실제 일은 kakaotalk 스킬 사본(mac_ax.py)이 한다.

읽기는 창이 최소화·숨김·다른 데스크탑에 있어도 되고, 보내기는 창이 화면에 보이거나
최소화·⌘H 숨김 상태면 된다 (카카오톡 26.8 실측). 창을 앞으로 가져오지 않고 클립보드를 쓰지 않는다.
"""
from __future__ import annotations

from kakao_read.backends import mac_ax
from kakao_read.backends.base import BackendError, ChatMessage, SendUncertain, Snapshot


class MacBackend:
    name = "macOS 접근성(AX)"

    def __init__(self) -> None:
        self._ax = None

    def _get_ax(self):
        # 카카오톡을 껐다 켜면 pid 가 바뀌므로 실패하면 다음 번에 새로 잡는다
        if self._ax is None:
            try:
                self._ax = mac_ax.AX()
            except mac_ax.KakaoError as exc:
                raise BackendError(str(exc)) from exc
        return self._ax

    def _call(self, fn, *args):
        ax = self._get_ax()
        try:
            return fn(ax, *args)
        except mac_ax.Uncertain as exc:
            raise SendUncertain(str(exc)) from exc
        except mac_ax.KakaoError as exc:
            if "실행 중이 아닙니다" in str(exc):
                self._ax = None
            raise BackendError(str(exc)) from exc
        except Exception as exc:  # 카카오톡이 꺼지면 AX 호출이 엉뚱한 오류를 낸다
            self._ax = None
            raise BackendError(f"카카오톡 창을 다루다 오류가 났습니다: {exc}") from exc

    def windows(self) -> list[tuple[str, str]]:
        def run(ax):
            return [(ax.attr(w, "AXTitle") or "", state) for w, state in ax.all_windows()]
        return self._call(run)

    def read(self, room: str, count: int) -> Snapshot:
        msgs, scrolled = self._call(mac_ax.read_room, room, count)
        return Snapshot(
            [ChatMessage(m.text, m.sender, m.outgoing, m.system, m.thread_comment, m.time) for m in msgs],
            scrolled,
        )

    def send(self, room: str, text: str) -> str:
        return self._call(mac_ax.send_plain, room, text)
