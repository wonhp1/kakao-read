"""방 프로필: 처음 보는 방은 지난 대화 전부로 맥락·말투를 정리해 저장하고, 그다음부터는 저장본 + 새 메시지만."""
from __future__ import annotations

from kakao_read import profile
from kakao_read.backends.base import ChatMessage, Snapshot
from kakao_read.bot import Bot
from kakao_read.config import Config

ROOM = "영희"


def inc(t):
    return ChatMessage(t, "영희", False)


def mine(t):
    return ChatMessage(t, "철수", True)


OLD = [mine("주말에 뭐해?"), inc("응!"), mine("ㅋㅋㅋ 그럼"), mine("영화볼래?"), inc("좋아좋아")]


class HistoryBackend:
    name = "fake"

    def __init__(self):
        self.screen = list(OLD)
        self.history_calls = 0

    def history(self, room, max_messages, progress=None):
        self.history_calls += 1
        return list(self.screen)[-max_messages:]

    def read(self, room, count):
        return Snapshot(self.screen[-count:])

    def send(self, room, text):
        self.screen.append(mine(text))
        return "전송 확인"


class Engine:
    def __init__(self):
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        if "다섯 칸" in user:
            return "[관계·호칭]\n친구\n[나의 말투]\n반말, 짧게 끊어 보냄, ㅋㅋ 자주"
        return "ㅋㅋ 그래"


def make(tmp_path, **kw):
    cfg = Config(watch=[ROOM], allowed_send=[ROOM], dry_run=False, trigger="all", prefix="",
                 profile_dir=str(tmp_path / "profiles"), **kw)
    backend, engine, logs = HistoryBackend(), Engine(), []
    return Bot(cfg, backend, engine, log=logs.append), backend, engine, cfg


def test_first_run_reads_all_history_and_saves_profile(tmp_path):
    bot, backend, engine, cfg = make(tmp_path)
    bot.tick()
    path = profile.path_for(cfg.profile_folder(), ROOM)
    saved = path.read_text(encoding="utf-8")
    assert backend.history_calls == 1
    assert "[나의 말투]" in saved and "반말" in saved
    assert "- 영화볼래?" in saved                       # 내 실제 메시지 예시
    assert "좋아좋아" not in saved.split("[나의 실제 메시지 예시]")[1]   # 상대 글은 예시에 안 들어감
    assert len(engine.calls) == 1                        # 정리만 했고, 화면에 있던 글에는 답하지 않았다


def test_after_first_run_only_new_messages_and_profile_used(tmp_path):
    bot, backend, engine, _ = make(tmp_path)
    bot.tick()
    backend.screen.append(inc("머해"))
    bot.tick()
    assert backend.history_calls == 1                    # 지난 대화는 다시 읽지 않는다
    system, user = engine.calls[-1]
    assert "반말, 짧게 끊어 보냄" in system and "나의 말투" in system
    assert "<답할 메시지>\n[영희] 머해" in user
    assert backend.screen[-1].text == "ㅋㅋ 그래"


def test_saved_profile_is_reused_on_next_start(tmp_path):
    bot, backend, engine, _ = make(tmp_path)
    bot.tick()
    bot2, backend2, engine2, _ = make(tmp_path)
    bot2.tick()
    assert backend2.history_calls == 0 and engine2.calls == []


def test_learn_history_off_skips(tmp_path):
    bot, backend, engine, cfg = make(tmp_path, learn_history=False)
    bot.tick()
    assert backend.history_calls == 0 and engine.calls == []
    assert not profile.path_for(cfg.profile_folder(), ROOM).exists()


def test_transcript_keeps_recent_when_long():
    msgs = [inc(f"메시지{i}") for i in range(1000)]
    text = profile.transcript(msgs, max_chars=200)
    assert "메시지999" in text and "메시지0 " not in text and text.startswith("(… 이전 대화는")


def test_path_for_sanitizes_room_name(tmp_path):
    assert profile.path_for(tmp_path, '스터디/방: "A"').name == "스터디_방_A.md"
