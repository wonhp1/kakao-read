"""가짜 카카오톡 창·가짜 엔진으로 봇을 시험한다. 실제 카카오톡·Claude 호출·전송은 없다."""
from __future__ import annotations

from pathlib import Path

import pytest

from kakao_read.backends.base import BackendError, ChatMessage, SendUncertain, Snapshot
from kakao_read.bot import SKIP, Bot, build_user, group_incoming, tidy
from kakao_read.config import Config, ConfigError, load
from kakao_read.engine import ClaudeCli
from kakao_read.tracker import RoomTracker, overlap

ROOM = "시험방"


def inc(text, sender="민아"):
    return ChatMessage(text, sender, False)


def out(text):
    return ChatMessage(text, None, True)


class FakeBackend:
    name = "fake"

    def __init__(self):
        self.screen: list[ChatMessage] = []
        self.sent: list[tuple[str, str]] = []
        self.fail_send: list[Exception] = []
        self.scrolled = False

    def windows(self):
        return [(ROOM, "보임")]

    def read(self, room, count):
        return Snapshot(self.screen[-count:], self.scrolled)

    def send(self, room, text):
        if self.fail_send:
            raise self.fail_send.pop(0)
        self.sent.append((room, text))
        self.screen.append(out(text))     # 보낸 답은 '내가 보낸 글'로 화면에 뜬다
        return "전송 확인"


class FakeEngine:
    def __init__(self, answer="네, 됩니다"):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.answer


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    defaults = dict(watch=[ROOM], allowed_send=[ROOM], dry_run=False, trigger="all", prefix="")
    defaults.update(kw)
    cfg = Config(**defaults)
    backend, engine, clock, logs = FakeBackend(), FakeEngine(), Clock(), []
    bot = Bot(cfg, backend, engine, log=logs.append, clock=clock)
    return bot, backend, engine, clock, logs


# --- 새 메시지 가려내기 ----------------------------------------------------------


def test_overlap_finds_sliding_window():
    assert overlap([1, 2, 3, 4], [3, 4, 5]) == 2
    assert overlap([1, 2, 3], [1, 2, 3]) == 3
    assert overlap([1, 2, 3], [7, 8]) == 0


def test_tracker_first_read_is_baseline_and_then_new_only():
    t = RoomTracker()
    first = [inc("a"), inc("b")]
    assert t.update(first) is None
    assert t.update(first) == []
    assert [m.text for m in t.update([inc("b"), inc("c"), inc("d")])] == ["c", "d"]


def test_tracker_ignores_moving_time_label_and_lost_sender():
    t = RoomTracker()
    t.update([ChatMessage("a", "민아", False, time="14:00")])
    # 다음 줄이 붙으면 시각 라벨이 옮겨 가고, 창이 밀리면 맨 위 줄 이름표가 사라질 수 있다
    new = t.update([ChatMessage("a", None, False), ChatMessage("b", "민아", False, time="14:01")])
    assert [m.text for m in new] == ["b"]


def test_tracker_resets_when_flow_lost():
    t = RoomTracker()
    t.update([inc("a"), inc("b")])
    assert t.update([inc("x"), inc("y")]) is None


# --- 묶기·조건 ------------------------------------------------------------------


def test_group_consecutive_same_sender_and_break_on_others():
    new = [inc("안녕하세요"), inc("질문 있어요"), out("네"), inc("저도요", "철수"),
           ChatMessage("철수님이 들어왔습니다.", None, False, system=True), inc("또", "철수")]
    groups = group_incoming(new)
    assert [(i, [m.text for m in g]) for i, g in groups] == [
        (0, ["안녕하세요", "질문 있어요"]), (3, ["저도요"]), (5, ["또"])]


def test_thread_comment_and_system_not_answered():
    bot, backend, engine, *_ = make()
    bot.tick()
    backend.screen += [ChatMessage("댓글", "민아", False, thread_comment=True),
                       ChatMessage("민아님이 나갔습니다.", None, False, system=True)]
    bot.tick()
    assert engine.calls == []


def test_keyword_trigger():
    bot, backend, engine, *_ = make(trigger="keyword", keywords=["!AI"])
    bot.tick()
    backend.screen += [inc("그냥 잡담"), inc("다른 이야기", "철수")]
    bot.tick()
    assert engine.calls == []
    backend.screen.append(inc("!ai 환불 돼요?", "영희"))
    bot.tick()
    assert len(engine.calls) == 1 and backend.sent == [(ROOM, "네, 됩니다")]


# --- 답 만들기·보내기 -------------------------------------------------------------


def test_first_read_does_not_answer_history_then_answers_new():
    bot, backend, engine, *_ = make()
    backend.screen = [inc("옛 질문?")]
    bot.tick()
    assert engine.calls == [] and backend.sent == []
    backend.screen.append(inc("새 질문?"))
    bot.tick()
    assert backend.sent == [(ROOM, "네, 됩니다")]
    bot.tick()        # 봇의 답은 '내가 보낸 글'이라 다시 답하지 않는다
    assert len(backend.sent) == 1


def test_prompt_has_context_and_question_separated():
    bot, backend, engine, *_ = make(context_messages=2)
    backend.screen = [inc("1"), inc("2", "철수"), inc("3", "영희")]
    bot.tick()
    backend.screen.append(inc("질문?"))
    bot.tick()
    user = engine.calls[0][1]
    ctx = user.split("<최근 대화")[1].split("</최근 대화>")[0]
    assert "[철수] 2" in ctx and "[영희] 3" in ctx and "[민아] 1" not in ctx
    assert "<답할 메시지>\n[민아] 질문?\n</답할 메시지>" in user


def test_dry_run_and_not_allowed_rooms_only_preview():
    for kw in ({"dry_run": True}, {"allowed_send": []}):
        bot, backend, engine, _, logs = make(**kw)
        bot.tick()
        backend.screen.append(inc("질문?"))
        bot.tick()
        assert backend.sent == [] and len(engine.calls) == 1
        assert any("[미리보기" in line for line in logs)


def test_skip_answer_is_not_sent():
    bot, backend, engine, *_ = make()
    engine.answer = SKIP
    bot.tick()
    backend.screen.append(inc("ㅋㅋㅋ"))
    bot.tick()
    assert backend.sent == []


def test_uncertain_send_is_never_retried():
    bot, backend, engine, _, logs = make()
    bot.tick()
    backend.fail_send = [SendUncertain("확인 못 함")]
    backend.screen.append(inc("질문?"))
    bot.tick()
    bot.tick()
    assert backend.sent == [] and bot.pending == []
    assert any("다시 보내지 않습니다" in line for line in logs)


def test_blocked_send_retries_next_tick_then_gives_up():
    bot, backend, engine, *_ = make()
    bot.tick()
    backend.fail_send = [BackendError("입력창에 작성 중인 글이 있어 덮어쓰지 않습니다")]
    backend.screen.append(inc("질문?"))
    bot.tick()
    assert backend.sent == [] and len(bot.pending) == 1
    bot.tick()
    assert backend.sent == [(ROOM, "네, 됩니다")] and bot.pending == []

    backend.fail_send = [BackendError("x")] * 3
    backend.screen.append(inc("두 번째?"))
    for _ in range(4):
        bot.tick()
    assert len(backend.sent) == 1 and bot.pending == []


def test_rate_limit_per_minute():
    bot, backend, engine, clock, _ = make(max_per_minute=2)
    bot.tick()
    for i in range(3):
        backend.screen.append(inc(f"질문{i}?", f"사람{i}"))
    bot.tick()
    assert len(backend.sent) == 2
    clock.t += 61
    backend.screen.append(inc("다음?", "사람9"))
    bot.tick()
    assert len(backend.sent) == 3


def test_same_text_same_sender_not_answered_twice_within_hour():
    bot, backend, engine, clock, _ = make()
    bot.tick()
    backend.screen.append(inc("질문?"))
    bot.tick()
    backend.screen.append(inc("질문?"))
    bot.tick()
    assert len(engine.calls) == 1
    clock.t += 3601
    backend.screen.append(inc("질문?"))
    bot.tick()
    assert len(engine.calls) == 2


def test_scrolled_up_room_waits_without_moving_baseline():
    bot, backend, engine, *_ = make()
    bot.tick()
    backend.scrolled = True
    backend.screen.append(inc("질문?"))
    bot.tick()
    assert engine.calls == []
    backend.scrolled = False
    bot.tick()
    assert len(engine.calls) == 1


def test_read_error_does_not_stop_other_rooms():
    bot, backend, engine, _, logs = make(watch=["없는 방", ROOM], allowed_send=[ROOM])

    real = backend.read

    def read(room, count):
        if room == "없는 방":
            raise BackendError("창을 찾을 수 없습니다")
        return real(room, count)

    backend.read = read
    bot.tick()
    backend.screen.append(inc("질문?"))
    bot.tick()
    assert backend.sent and any("읽기 실패" in line for line in logs)


def test_tidy_trims_quotes_prefix_and_length():
    cfg = Config(prefix="🤖 ", max_chars=20)
    assert tidy('"안녕하세요"', cfg) == "🤖 안녕하세요"
    assert tidy("  ", cfg) is None
    long = tidy("가" * 50, cfg)
    assert long.startswith("🤖 ") and len(long) == len("🤖 ") + 20 and long.endswith("…")


def test_user_prompt_marks_owner_and_notice():
    text = build_user(ROOM, [out("공지 읽어 주세요"), ChatMessage("입장", None, False, system=True)],
                      [inc("질문?")])
    assert "[나(방 주인)] 공지 읽어 주세요" in text and "[공지] 입장" in text


# --- 엔진 격리 --------------------------------------------------------------------


def test_claude_cli_argv_disables_tools_and_personal_config():
    argv = ClaudeCli("haiku").argv("claude", "SYS")
    assert argv[:2] == ["claude", "-p"]
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    for flag in ("--safe-mode", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--system-prompt") + 1] == "SYS"
    assert "--allowedTools" not in argv


# --- 설정 -----------------------------------------------------------------------


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_example_config_loads_safe_defaults():
    cfg = load(Path(__file__).resolve().parent.parent / "config.example.toml")
    assert cfg.dry_run is True and cfg.allowed_send == [] and cfg.trigger == "keyword"
    assert not cfg.can_send(cfg.watch[0])


def test_config_rejects_send_room_not_watched(tmp_path):
    with pytest.raises(ConfigError):
        load(write(tmp_path, '[rooms]\nwatch=["a"]\nallowed_send=["b"]\n'))


def test_config_rejects_bad_trigger_and_fast_poll(tmp_path):
    with pytest.raises(ConfigError):
        load(write(tmp_path, '[reply]\ntrigger="mention"\n'))
    with pytest.raises(ConfigError):
        load(write(tmp_path, '[reply]\npoll_seconds=1\n'))


def test_knowledge_files_relative_to_config(tmp_path):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "faq.md").write_text("환불은 7일 안에", encoding="utf-8")
    cfg = load(write(tmp_path, '[ai]\nknowledge_files=["kb/faq.md", "없음.md"]\n'))
    text, warnings = cfg.knowledge()
    assert "환불은 7일 안에" in text and len(warnings) == 1
    bot = Bot(cfg, FakeBackend(), FakeEngine(), log=lambda _: None)
    assert "환불은 7일 안에" in bot.system
