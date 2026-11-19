"""doctor: 안 된 것을 고치는 법까지 '다음 할 일'로 알려 주는지 (가짜 백엔드·가짜 Claude 로)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kakao_read import cli

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"


class FakeBackend:
    name = "fake"
    non_rooms = {"카카오톡"}

    def __init__(self, checks, titles=("카카오톡", "민아")):
        self._checks, self._titles, self.windows_called = checks, titles, False

    def checks(self):
        return self._checks

    def windows(self):
        self.windows_called = True
        return [(t, "보임") for t in self._titles]


@pytest.fixture
def doctor(monkeypatch, capsys):
    def run(backend, logged=True, installed=True):
        monkeypatch.setattr(cli, "get_backend", lambda: backend)
        monkeypatch.setattr(cli, "find_claude", lambda path="": "/bin/claude" if installed else None)
        monkeypatch.setattr(cli, "version", lambda b: "2.1.0")
        monkeypatch.setattr(cli, "auth_status", lambda b: (logged, "로그인 됨" if logged else "로그인 안 됨"))
        rc = cli.main(["doctor", "--config", str(EXAMPLE)])
        return rc, capsys.readouterr().out
    return run


def test_all_ready_suggests_first_try_with_a_real_room(doctor):
    rc, out = doctor(FakeBackend([(True, "카카오톡", "26.9.0 (실측한 판)", "")]))
    assert rc == 0 and "다음 할 일" not in out
    assert "열린 방 창 1개: 민아" in out            # 친구 목록 창은 방으로 세지 않는다
    assert 'reply "민아" --dry' in out


def test_missing_permission_lists_fix_and_skips_window_read(doctor):
    backend = FakeBackend([(True, "카카오톡", "26.9.0", ""),
                           (False, "손쉬운 사용 권한", "터미널 에 꺼져 있음", "손쉬운 사용 에서 '터미널' 을 켜세요")])
    rc, out = doctor(backend, logged=False)
    assert rc == 1 and not backend.windows_called
    todo = out.split("다음 할 일")[1]
    assert "1. Claude 로그인" in todo and "2. 손쉬운 사용 에서 '터미널' 을 켜세요" in todo


def test_other_kakao_version_is_a_warning_not_a_blocker(doctor):
    rc, out = doctor(FakeBackend([(None, "카카오톡", "27.1.0 — 실측한 판과 달라", "")]))
    assert rc == 0 and "⚠️  카카오톡: 27.1.0" in out


def test_no_room_window_tells_how_to_open_one(doctor):
    rc, out = doctor(FakeBackend([], titles=("카카오톡",)))
    assert rc == 1 and "더블클릭해 별도 창으로" in out.split("다음 할 일")[1]


def test_claude_not_installed(doctor):
    rc, out = doctor(FakeBackend([]), installed=False)
    assert rc == 1 and "Claude Code 가 설치돼 있지 않습니다" in out


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS 전용")
def test_mac_checks_report_kakao_state_and_permission(monkeypatch):
    import ApplicationServices

    from kakao_read.backends import mac_ax
    from kakao_read.backends.mac import MacBackend

    monkeypatch.setattr(mac_ax, "_host_app", lambda: "터미널")
    monkeypatch.setattr(ApplicationServices, "AXIsProcessTrusted", lambda: False)
    cases = {(False, None): (False, "설치돼 있지 않습니다"), (False, "26.9.0"): (False, "꺼져 있습니다"),
             (True, "26.9.0"): (True, "실측한 판"), (True, "27.1.0"): (None, "실측한 판(26.8·26.9)과 달라")}
    for state, (good, words) in cases.items():
        monkeypatch.setattr(mac_ax, "_kakao_version", lambda s=state: s)
        checks = {item: (g, detail, fix) for g, item, detail, fix in MacBackend().checks()}
        assert checks["카카오톡"][0] is good and words in checks["카카오톡"][1]
        assert checks["손쉬운 사용 권한"][0] is False and "'터미널' 을 켜고" in checks["손쉬운 사용 권한"][2]
