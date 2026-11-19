"""윈도우 백엔드: 복사한 대화 글 나누기와 보내기 재시도 (가짜 카카오톡으로, 어느 OS 에서나 돈다)."""
from __future__ import annotations

import pytest

from kakao_read.backends import windows as W
from kakao_read.backends.base import BackendError, SendUncertain

COPIED = """2026년 11월 19일 목요일
[민아] [오후 3:11] 첫 줄
둘째 줄
[철수] [오후 3:45] 안녕
영희님이 들어왔습니다.
[민아] [오후 9:11] 링크"""


def test_parse_groups_lines_and_skips_dates():
    msgs = W.parse(COPIED)
    assert [(m.sender, m.text, m.time, m.system) for m in msgs] == [
        ("민아", "첫 줄\n둘째 줄", "오후 3:11", False),
        ("철수", "안녕", "오후 3:45", False),
        (None, "영희님이 들어왔습니다.", None, True),
        ("민아", "링크", "오후 9:11", False),
    ]


class FakeKakao:
    """입력창 하나와 대화 목록. enter_works 의 값 차례로 Enter 를 처리한다."""

    def __init__(self, enter_works: list[str]):
        self.box = ""
        self.log = ["[민아] [오후 9:11] 링크"]
        self.enter_works = list(enter_works)   # "send" · "ignore" · "newline" · "eat"
        self.enters = 0

    # _Win32 흉내
    class U:
        @staticmethod
        def IsIconic(_):
            return False

    u = U()

    def child(self, hwnd, cls):
        return 2

    def get_text(self, _):
        return self.box or W.PLACEHOLDER

    def set_text(self, _, text):
        self.box = text                 # 진짜 카카오톡처럼: WM_SETTEXT 만으로는 입력으로 치지 않는다
        if not text:
            self.typed = False
        return True

    typed = False

    def type_chars(self, _, text):
        self.box += text
        self.typed = True

    def key_direct(self, _, vk):
        self.box += "\r\n"              # 창 프로시저로 바로 간 Enter 는 줄바꿈

    def key(self, _, vk):
        assert vk == W.VK_RETURN
        self.enters += 1
        how = self.enter_works.pop(0) if self.enter_works else "ignore"
        if how == "send" and not self.typed:
            how = "ignore"              # WM_CHAR 로 친 적 없으면 Enter 를 먹지 않는다
        if how == "send":
            self.log.append(f"[철수] [오후 1:05] {self.box}")
            self.box = ""
        elif how == "newline":
            self.box += "\r\n"
        elif how == "eat":          # 글은 사라졌는데 말풍선이 없다
            self.box = ""


def backend(fake: FakeKakao) -> W.WindowsBackend:
    b = W.WindowsBackend.__new__(W.WindowsBackend)
    b.w, b.my_name = fake, None
    b._room = lambda room: 1
    b._copy_all = lambda hwnd: "\n".join(fake.log)
    return b


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(W.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(W.time, "monotonic", lambda: clock["t"])


def test_send_first_try():
    fake = FakeKakao(["send"])
    assert "전송 확인" in backend(fake).send("민아", "ㅋㅋ")
    assert fake.enters == 1 and fake.log[-1].endswith("ㅋㅋ")


def test_send_sends_exact_text_without_trailing_newline():
    fake = FakeKakao(["send"])
    backend(fake).send("민아", "줄1\n줄2")
    assert fake.log[-1] == "[철수] [오후 1:05] 줄1\r\n줄2"


def test_send_retries_until_it_goes():
    fake = FakeKakao(["ignore", "newline", "send"])
    result = backend(fake).send("민아", "test")
    assert "3번째 시도" in result
    assert fake.enters == 3
    assert sum(line.endswith("test") for line in fake.log) == 1   # 딱 한 번만 나감


def test_send_gives_up_after_three_and_clears_box():
    fake = FakeKakao(["ignore", "ignore", "ignore"])
    with pytest.raises(BackendError, match="3번"):
        backend(fake).send("민아", "test")
    assert fake.enters == 3 and fake.box == ""


def test_send_does_not_retry_when_text_vanished():
    fake = FakeKakao(["eat", "send"])
    with pytest.raises(SendUncertain):
        backend(fake).send("민아", "test")
    assert fake.enters == 1   # 나갔을 수 있으니 다시 누르지 않는다


def test_send_refuses_to_overwrite_draft():
    fake = FakeKakao(["send"])
    fake.box = "쓰다 만 글"
    with pytest.raises(BackendError, match="쓰다 만"):
        backend(fake).send("민아", "test")
    assert fake.enters == 0
