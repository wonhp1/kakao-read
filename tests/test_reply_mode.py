"""답장 방식: command(명령할 때만, kakao-read reply) · auto(run 이 알아서)."""
from __future__ import annotations

import pytest

from kakao_read import cli
from kakao_read.backends.base import BackendError, ChatMessage, Snapshot
from kakao_read.bot import build_system, compose
from kakao_read.config import Config, ConfigError, load


def inc(t, who="영희"):
    return ChatMessage(t, who, False)


def mine(t):
    return ChatMessage(t, "철수", True)


class Backend:
    def __init__(self, screen):
        self.screen = screen

    def read(self, room, count):
        return Snapshot(self.screen[-count:])


class Engine:
    def __init__(self, answer="ㅋㅋ 미안"):
        self.answer, self.calls = answer, []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.answer


def test_compose_answers_last_incoming_run():
    screen = [mine("주말에 뭐해?"), inc("응!"), inc("집에 있을 것 같아")]
    eng = Engine()
    draft = compose(Config(prefix=""), Backend(screen), eng, "영희", "sys")
    assert [m.text for m in draft.group] == ["응!", "집에 있을 것 같아"]   # 이어 보낸 글은 한 묶음
    assert draft.text == "ㅋㅋ 미안"
    user = eng.calls[0][1]
    assert "[나(방 주인)] 주말에 뭐해?" in user.split("<답할 메시지>")[0]


def test_compose_mentions_what_i_already_sent_and_request():
    screen = [inc("언제 와?"), mine("가는 중")]
    eng = Engine()
    compose(Config(prefix=""), Backend(screen), eng, "영희", "sys", "10분 뒤 도착한다고 해줘")
    user = eng.calls[0][1]
    assert "<그 뒤에 내가 이미 보낸 글>\n[나(방 주인)] 가는 중" in user
    assert "<방 주인의 요청>\n10분 뒤 도착한다고 해줘" in user


def test_compose_without_incoming_needs_request():
    with pytest.raises(BackendError, match="요청"):
        compose(Config(), Backend([mine("안녕")]), Engine(), "영희", "sys")
    draft = compose(Config(prefix=""), Backend([mine("안녕")]), Engine("잘자"), "영희", "sys", "잘자라고 해줘")
    assert draft.group == [] and draft.text == "잘자"


def test_no_prefix_by_default():
    draft = compose(Config(), Backend([inc("머해")]), Engine("ㅋㅋ 일해"), "영희", "sys")
    assert draft.text == "ㅋㅋ 일해"


def test_commanded_system_follows_owner_request():
    s = build_system(Config(), "", None, commanded=True)
    assert "방 주인의 요청" in s and "누군가 너를 불러서" not in s


def test_default_mode_is_command_and_invalid_mode_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[rooms]\nwatch = ["a"]\n', encoding="utf-8")
    assert load(p).mode == "command"
    p.write_text('[reply]\nmode = "sometimes"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="mode"):
        load(p)


def test_run_refuses_in_command_mode(tmp_path, capsys):
    p = tmp_path / "c.toml"
    p.write_text('[rooms]\nwatch = ["a"]\n[reply]\nmode = "command"\n', encoding="utf-8")
    assert cli.main(["run", "--config", str(p)]) == 1
    assert "kakao-read reply" in capsys.readouterr().err
