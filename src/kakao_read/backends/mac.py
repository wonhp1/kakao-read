"""macOS — 접근성(AX)으로 읽고 보낸다. 실제 일은 kakaotalk 스킬 사본(mac_ax.py)이 한다.

읽기는 창이 최소화·숨김·다른 데스크탑에 있어도 되고, 보내기는 창이 화면에 보이거나
최소화·⌘H 숨김 상태면 된다 (카카오톡 26.8 실측). 창을 앞으로 가져오지 않고 클립보드를 쓰지 않는다.
"""
from __future__ import annotations

from kakao_read.backends import mac_ax
from kakao_read.backends.base import BackendError, ChatMessage, SendUncertain, Snapshot


TESTED_VERSIONS = ("26.8", "26.9")   # 읽기·보내기를 실측한 카카오톡 판 (지난 대화 전부 읽기는 26.9)


class MacBackend:
    name = "macOS 접근성(AX)"
    non_rooms = {"카카오톡", "KakaoTalk", mac_ax.NOTICE_WINDOW}   # 대화방이 아닌 창 (친구 목록 · 댓글 알림)

    def __init__(self) -> None:
        self._ax = None

    def checks(self) -> list[tuple[bool | None, str, str, str]]:
        """처음 쓰는 사람이 해야 할 것 — (됨?, 항목, 상태, 고치는 법). 접근성 권한이 없어도 돈다.
        됨? 은 True 됨 · False 안 됨(필수) · None 참고."""
        import platform

        from ApplicationServices import AXIsProcessTrusted

        out: list[tuple[bool | None, str, str, str]] = [(True, "macOS", platform.mac_ver()[0] or "?", "")]
        running, ver = mac_ax._kakao_version()
        if ver is None:
            out.append((False, "카카오톡", "설치돼 있지 않습니다",
                        "App Store 에서 카카오톡을 설치하고 로그인하세요"))
        elif not running:
            out.append((False, "카카오톡", f"{ver} — 꺼져 있습니다", "카카오톡을 켜고 로그인하세요"))
        elif ver.startswith(TESTED_VERSIONS):
            out.append((True, "카카오톡", f"{ver} (실측한 판)", ""))
        else:
            out.append((None, "카카오톡", f"{ver} — 실측한 판({'·'.join(TESTED_VERSIONS)})과 달라 "
                        "화면 구조가 바뀌었으면 읽기·보내기가 안 될 수 있습니다", ""))
        host = mac_ax._host_app()
        if AXIsProcessTrusted():
            out.append((True, "손쉬운 사용 권한", f"{host} 에 켜져 있음", ""))
        else:
            out.append((False, "손쉬운 사용 권한", f"{host} 에 꺼져 있음 — 카카오톡 창을 읽을 수 없습니다",
                        f"시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용 에서 '{host}' 을 켜고 (목록에 없으면 + 로 추가), "
                        f"{host} 를 완전히 종료(⌘Q)했다가 다시 여세요"))
        return out

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

    def history(self, room: str, max_messages: int, progress=None) -> list[ChatMessage]:
        """방을 맨 위까지 올려 지난 대화를 모두 읽는다 (오래된 것부터). 끝나면 맨 아래로 되돌린다.
        창이 화면에 보여야 한다. 메시지 1300개쯤에 1분 안팎 (26.9 실측: 불러오기 25초 + 읽기 40초)."""
        msgs = self._call(mac_ax.read_history, room, max_messages, progress)
        return [ChatMessage(m.text, m.sender, m.outgoing, m.system, m.thread_comment, m.time) for m in msgs]

    def send(self, room: str, text: str) -> str:
        return self._call(mac_ax.send_plain, room, text)
