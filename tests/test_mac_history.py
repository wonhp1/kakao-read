"""맥 백엔드: 맨 위까지 불러와 한 화면씩 읽기 (가짜 카카오톡 표로, 어느 OS 에서나 돈다)."""
from __future__ import annotations

import pytest

from kakao_read.backends import mac_ax as M
from kakao_read.backends.mac import MacBackend

ROW_H, VIEW_H, LOAD = 40, 400, 50   # 줄 높이, 보이는 높이, 맨 위로 올릴 때 붙는 줄 수 (26.9 실측과 비슷하게)


class Row:
    def __init__(self, data):
        self.data = data              # (본문, 이름표, 보낸 쪽) — 이름표 없는 줄은 앞 사람을 물려받는다


class FakeAX:
    """방 창의 메시지 표. 처음엔 최근 줄만 붙어 있고, 스크롤바를 맨 위로 맞추면 예전 줄이 LOAD 개씩 붙는다."""

    def __init__(self, history: list[tuple], loaded: int):
        self.older = list(history[:-loaded])
        self.rows = [Row(d) for d in history[-loaded:]]
        self.value = 1.0
        self.parsed: list[int] = []   # 읽은 줄 번호 (보이는 줄만 읽었는지 확인용)

    # mac_ax 가 쓰는 것만
    def window(self, room, need="any"):
        assert need == "visible"      # 스크롤은 창이 화면에 보일 때만
        return "win"

    def kids(self, e):
        if e == "table":
            return list(self.rows)
        if isinstance(e, Row):
            return [("cell", e)]
        return []

    def set_attr(self, e, name, value):
        assert (e, name) == ("bar", "AXValue")
        self.value = value
        if value == 0.0 and self.older:
            take, self.older = self.older[-LOAD:], self.older[:-LOAD]
            self.rows = [Row(d) for d in take] + self.rows
        return 0

    def attr(self, e, name):
        if isinstance(e, Row):
            return {"AXRole": "AXRow", "AXIndex": self.rows.index(e)}.get(name)
        if (e, name) == ("table", "AXVisibleRows"):
            top = self.value * max(0, len(self.rows) * ROW_H - VIEW_H)
            return [r for i, r in enumerate(self.rows) if top - ROW_H < i * ROW_H < top + VIEW_H]
        return None


@pytest.fixture
def fake(monkeypatch):
    def make(history, loaded):
        ax = FakeAX(history, loaded)
        monkeypatch.setattr(M, "message_area", lambda a, w: ("area", "table", "bar"))
        monkeypatch.setattr(M, "rect", lambda a, e: (0, 0, 100, VIEW_H) if e == "area"
                            else (0, 0, 100, len(ax.rows) * ROW_H))

        def parse(a, cell):
            row = cell[1]
            ax.parsed.append(ax.rows.index(row))
            text, label, outgoing = row.data
            return text, label, outgoing, None, False

        monkeypatch.setattr(M, "_parse_cell", parse)
        monkeypatch.setattr(M, "HISTORY_WAIT", 0.01)
        monkeypatch.setattr(M.time, "sleep", lambda s: None)
        return ax
    return make


def chat(n):
    """민아·나가 번갈아, 민아는 두 줄씩 (두 번째 줄은 이름표 없음)."""
    out = []
    for i in range(n):
        if i % 3 == 0:
            out.append((f"m{i}", "민아", False))
        elif i % 3 == 1:
            out.append((f"m{i}", None, False))
        else:
            out.append((f"m{i}", None, True))
    return out


def test_loads_to_the_beginning_and_reads_every_message_in_order(fake):
    ax = fake(chat(230), loaded=30)
    msgs = M.read_history(ax, "방", 2000)
    assert [m.text for m in msgs] == [f"m{i}" for i in range(230)]
    assert ax.value == 1.0                                # 맨 아래로 되돌렸다
    assert sorted(set(ax.parsed)) == list(range(230))     # 모든 줄을 읽었고
    assert len(ax.parsed) == 230                          # 한 줄도 두 번 읽지 않았다


def test_sender_label_carries_to_following_lines(fake):
    ax = fake(chat(9), loaded=9)
    msgs = M.read_history(ax, "방", 2000)
    assert [(m.text, m.sender, m.outgoing) for m in msgs[:3]] == [
        ("m0", "민아", False), ("m1", "민아", False), ("m2", None, True)]


def test_stops_loading_at_cap_and_keeps_most_recent(fake):
    ax = fake(chat(1000), loaded=30)
    msgs = M.read_history(ax, "방", 120)
    assert len(msgs) == 120 and msgs[-1].text == "m999"
    assert len(ax.rows) < 300                             # 한도 근처에서 불러오기를 멈췄다
    assert ax.older                                       # (처음까지는 안 갔다)


def test_restores_scroll_even_when_reading_fails(fake, monkeypatch):
    ax = fake(chat(100), loaded=30)

    def boom(a, cell):
        raise RuntimeError("카카오톡이 꺼짐")

    monkeypatch.setattr(M, "_parse_cell", boom)
    with pytest.raises(RuntimeError):
        M.read_history(ax, "방", 2000)
    assert ax.value == 1.0


def test_mac_backend_history_maps_messages(monkeypatch):
    seen = {}

    def fake_history(ax, room, max_messages, progress):
        seen.update(room=room, max=max_messages)
        return [M.Message("안녕", "민아", False, "14:00", False, False), M.Message("응", None, True, None, False, False)]

    monkeypatch.setattr(M, "read_history", fake_history)
    backend = MacBackend()
    backend._ax = object()
    msgs = backend.history("방", 500)
    assert seen == {"room": "방", "max": 500}
    assert [(m.text, m.sender, m.outgoing, m.time) for m in msgs] == [("안녕", "민아", False, "14:00"), ("응", None, True, None)]
