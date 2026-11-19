# kakaotalk 스킬(scripts/kakaotalk.py)의 사본이다. 맥 백엔드(mac.py)가 read_room·send_plain 을 쓴다.
# 스킬 쪽을 고치면 이 파일도 같이 맞춘다. 단독 실행(`python -m kakao_read.backends.mac_ax ...`)도 된다.
"""macOS 카카오톡 앱을 접근성(AX)으로 읽고 보낸다. 서버 접속·로그인·데이터 파일 복호화 없음.

kakaobot 프로젝트(github.com/wonhp1/kakaobot, 2026-09~10)에서 실측으로 확정한 방법을 떼어 왔다.
카카오톡 25.9.x 에서 실측, 26.8 에서 읽기·사진 보내기 다시 확인.

  처음이면
    guide [--auto]                   준비 상태 점검(권한·카카오톡·창·허용 목록)과 사용법
    set-confirm on|off               보내기 전 미리보기·확인 (기본 on)

  읽기 (창을 건드리지 않는다 · 최소화·⌘H 숨김·다른 데스크탑·전체 화면 뒤의 창도)
    windows                          열린 카카오톡 창 제목
    read    "방" [-n 20]             방 창의 최근 메시지
    notices "방" [--top 20]          '댓글 알림' 창에서 그 방 알림만

  화면 조작 (메뉴·스레드 창을 잠깐 연다 — 마우스·포커스는 움직이지 않는다 · 창이 화면에 보여야 함)
    thread  "방" "원글"              원글의 스레드(댓글)를 열어 읽고 닫는다
    photo   "방" "옆 메시지"         사진을 '저장하기'로 받아 글자를 읽고 파일은 지운다

  보내기 (전송 허용 목록의 방만 · --yes 없으면 미리보기만(set-confirm off 면 바로) · send 는 최소화·숨김 창에도)
    send    "방" "글" [--yes]        일반 메시지
    reply   "방" "원글" "글" [--yes] 원글에 댓글(스레드)로. 기본은 '채팅방에 함께 보내기' 켬
    send-file "방" 파일 [--yes]      사진·파일 하나 (카카오톡 '파일전송' 창으로)

모두 --json 을 붙이면 JSON 으로 낸다.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import struct
import subprocess
import sys
import time
import tomllib
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

KAKAO_BUNDLE = "com.kakao.KakaoTalkMac"
NOTICE_WINDOW = "댓글 알림"
UNREAD_ICON = "common icon unread mention"
MIRROR_CHECKBOX = "채팅방에 함께 보내기"
AUX_WINDOW = "댓글 달린 메시지"       # 스레드를 열면 함께 뜨는 부속 창
SAVE_ITEM = "저장하기"
ZWSP = chr(0x200B)

# 전송 허용 목록 — kakaobot·openkakao-cli 와 같은 파일을 쓴다 (방 이름 정확히 일치)
ALLOWLIST = Path.home() / ".config" / "openkakao" / "config.toml"
# 이 스킬만의 설정·첫 안내 표시. openkakao-cli 가 모르는 키를 싫어할 수 있어 파일을 따로 둔다.
SKILL_HOME = Path.home() / ".config" / "kakaotalk-skill"
SETTINGS = SKILL_HOME / "config.toml"
ONBOARDED = SKILL_HOME / "onboarded"

# 함께 보낸 스레드 댓글: 말풍선 위 원글 인용 칸 → 글 상자가 이만큼 넘게 내려앉는다
# (보통 13~18, 함께 보낸 댓글 62~67). 말풍선 아래 스레드 버튼 높이는 22.
QUOTE_GAP = 40
LINK_MAX_H = 30

# 사진: 이보다 작은 그림(프로필·이모티콘)은 사진이 아니다. 저장 파일 이름은 카카오톡이 정한다
MIN_PHOTO_PX = 60
PHOTO_GLOB = "KakaoTalk_Photo_*"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp"}
OCR_LANGUAGES = ("ko-KR", "en-US")
OCR_MIN_CONFIDENCE = 0.30

RETURN_KEYCODE = 36  # kVK_Return

# 지금 화면에 없는 창(최소화·⌘H 숨김·다른 데스크탑·전체 화면 뒤)은 AXWindows 에 안 나온다 (최소화는 나옴).
# macOS 비공개 함수 _AXUIElementCreateWithRemoteToken 에 원소 번호를 하나씩 넣어 창을 직접 만든다
# (AltTab 같은 창 전환 앱이 쓰는 방법). 번호는 창을 새로 열수록 커진다 (26.8 실측 557·558·611·613).
# 먼저 앞쪽만 훑고, 못 찾으면 더 멀리 (0~4000 은 0.1초, 0~40000 은 1~2초).
HISERVICES = "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework"
REMOTE_MAGIC = 0x636F636F            # 'coco' — Cocoa 앱 원소 표식
WINDOW_SCAN = (4000, 40000)

# 시스템 공지는 구조로 구분되지 않아 문구로 가른다 (연속 메시지 가운데 줄과 구조가 같다)
SYSTEM_NOTICE = re.compile(
    r"님이 (들어왔|나갔)습니다|님을 (초대했|내보냈)습니다|님이 방장이 되었습니다"
    r"|관리자가 되었습니다|공지가 (등록|수정)되었습니다|오픈채팅방입니다|^여기까지 읽었습니다\.?$"
)


class KakaoError(Exception):
    """아무것도 보내지 않았다 (보내기 전 단계에서 멈춤)."""


class Uncertain(Exception):
    """전송 버튼을 누른 뒤 확인하지 못했다 — 나갔을 수 있다. 다시 보내지 말고 방을 확인한다."""


def _remote_token_fn():
    """비공개 함수 _AXUIElementCreateWithRemoteToken. 없으면 None — 그러면 화면 쪽 창만 찾는다."""
    try:
        import objc
        from Foundation import NSBundle

        found: dict = {}
        objc.loadBundleFunctions(NSBundle.bundleWithPath_(HISERVICES), found, [
            ("_AXUIElementCreateWithRemoteToken", b"^{__AXUIElement=}@", "", {"retval": {"already_cfretained": True}}),
        ])
        return found.get("_AXUIElementCreateWithRemoteToken")
    except Exception:  # pragma: no cover — macOS 가 바뀌어 함수가 없어진 경우
        return None


# --- AX 저수준 -----------------------------------------------------------------


class AX:
    def __init__(self) -> None:
        try:
            from ApplicationServices import (
                AXIsProcessTrusted,
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
                AXUIElementPerformAction,
                AXUIElementSetAttributeValue,
            )
            from AppKit import NSWorkspace
        except ImportError as exc:  # pragma: no cover
            raise KakaoError("pyobjc 가 없습니다 — `uv run kakaotalk.py ...` 로 실행하세요") from exc
        if not AXIsProcessTrusted():
            raise KakaoError(
                "손쉬운 사용 권한이 없습니다 — 시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 "
                "이 명령을 실행하는 앱(터미널·Claude 등)을 켜고, 그 앱을 다시 여세요"
            )
        self._copy, self._perform, self._set = (
            AXUIElementCopyAttributeValue, AXUIElementPerformAction, AXUIElementSetAttributeValue,
        )
        pid = next(
            (a.processIdentifier() for a in NSWorkspace.sharedWorkspace().runningApplications()
             if a.bundleIdentifier() == KAKAO_BUNDLE),
            None,
        )
        if pid is None:
            raise KakaoError("카카오톡이 실행 중이 아닙니다")
        self.pid = pid
        self.app = AXUIElementCreateApplication(pid)
        self._remote = _remote_token_fn()

    def attr(self, e, name):
        err, v = self._copy(e, name, None)
        return v if err == 0 else None

    def kids(self, e) -> list:
        return list(self.attr(e, "AXChildren") or [])

    def perform(self, e, action) -> int:
        return self._perform(e, action)

    def set_attr(self, e, name, value) -> int:
        return self._set(e, name, value)

    def walk(self, e, depth=0, max_depth=14):
        yield e
        if depth < max_depth:
            for ch in self.kids(e):
                yield from self.walk(ch, depth + 1, max_depth)

    def windows(self) -> list:
        """지금 화면 쪽 창 (최소화한 창 포함). 숨김·다른 데스크탑·전체 화면 뒤의 창은 빠진다."""
        return list(self.attr(self.app, "AXWindows") or [])

    def _remote_windows(self, limit: int) -> list:
        """원소 번호 0..limit 로 만든 창 원소 — 화면에 없는 창까지. 비공개 함수가 없으면 빈 목록."""
        if self._remote is None:
            return []
        from Foundation import NSData

        out = []
        for eid in range(limit):
            token = NSData.dataWithBytes_length_(struct.pack("<iiIQ", self.pid, 0, REMOTE_MAGIC, eid), 20)
            e = self._remote(token)
            if e is not None and self.attr(e, "AXRole") == "AXWindow":
                out.append(e)
        return out

    def all_windows(self) -> list[tuple[object, str]]:
        """(창, 상태) — 상태는 '보임' · '최소화' · '화면에 없음'(⌘H 숨김·다른 데스크탑·전체 화면 뒤)."""
        shown = self.windows()
        out = [(w, "최소화" if self.attr(w, "AXMinimized") else "보임") for w in shown]
        for w in self._remote_windows(WINDOW_SCAN[0]):
            if not any(w == s for s in shown):
                out.append((w, "화면에 없음"))
        return out

    def window(self, title: str, need: str = "any"):
        """제목이 정확히 같은 창 하나. 화면에 없는 창(최소화·숨김·다른 데스크탑·전체 화면 뒤)도 찾는다.

        need — 그 조작이 창에 바라는 것 (26.8 실측):
          "any"     어디 있든 — 읽기
          "listed"  화면 쪽 목록에 있음: 보임·최소화·⌘H 숨김 — 글 보내기 ('전송' 버튼은 최소화·숨김
                    상태에서도 눌린다. 숨긴 앱의 창도 AXWindows 에 나온다)
          "visible" 화면에 보임 (최소화·숨김 아님) — 메뉴·시트·새 창을 띄우는 조작 (스레드·사진·파일)
        """
        shown = [w for w in self.windows() if self.attr(w, "AXTitle") == title]
        every: list = []
        for limit in WINDOW_SCAN:
            every = [w for w in self._remote_windows(limit) if self.attr(w, "AXTitle") == title]
            if every or shown:          # 보이는 창이 있어도 첫 범위는 훑는다 — 같은 제목 창이 숨어 있을 수 있다
                break
        wins = shown + [w for w in every if not any(w == s for s in shown)]
        if not wins:
            raise KakaoError(f"'{title}' 창을 찾을 수 없습니다 — 카카오톡에서 그 방을 더블클릭해 창으로 열어 "
                             "두세요 (닫은 창은 찾을 수 없습니다)")
        if len(wins) > 1:
            raise KakaoError(f"'{title}' 이름의 창이 {len(wins)}개입니다 — 어느 것인지 알 수 없습니다")
        win = wins[0]
        if need == "listed" and not shown:
            raise KakaoError(f"'{title}' 창이 지금 화면에 없습니다 (다른 데스크탑·전체 화면 뒤) — 읽기는 되지만, "
                             "글 보내기는 화면에 보이거나 최소화·숨김(⌘H) 상태의 창에만 합니다. 창을 꺼내 주세요")
        if need == "visible" and (not shown or self.attr(win, "AXMinimized") or self.attr(self.app, "AXHidden")):
            raise KakaoError(f"'{title}' 창이 지금 화면에 없습니다 (최소화·숨김·다른 데스크탑·전체 화면 뒤) — "
                             "읽기는 되지만, 스레드·사진·파일 보내기는 창이 화면에 보여야 합니다. 창을 꺼내 주세요")
        return win

    def press_return(self) -> None:
        """Return 키를 **카카오톡 프로세스에만** 보낸다 — 앱을 앞으로 가져오지 않는다."""
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPostToPid,
            CGEventSourceCreate,
            kCGEventSourceStateCombinedSessionState,
        )

        src = CGEventSourceCreate(kCGEventSourceStateCombinedSessionState)
        for down in (True, False):
            CGEventPostToPid(self.pid, CGEventCreateKeyboardEvent(src, RETURN_KEYCODE, down))


def _num(value, key: str) -> float | None:
    try:
        return float(str(value).split(f"{key}:")[1].split(" ")[0].rstrip(","))
    except (IndexError, ValueError):
        return None


def rect(ax: AX, e) -> tuple[float, float, float, float] | None:
    """(x, y, 너비, 높이). AXValue 구조체는 문자열로만 읽혀 숫자를 뽑는다."""
    pos, size = ax.attr(e, "AXPosition"), ax.attr(e, "AXSize")
    if pos is None or size is None:
        return None
    vals = (_num(pos, "x"), _num(pos, "y"), _num(size, "w"), _num(size, "h"))
    return None if None in vals else vals  # type: ignore[return-value]


def message_area(ax: AX, win):
    """메시지 목록 스크롤 영역, 그 안의 표, 세로 스크롤바."""
    for e in ax.walk(win, max_depth=8):
        if ax.attr(e, "AXRole") != "AXScrollArea":
            continue
        kids = ax.kids(e)
        table = next((k for k in kids if ax.attr(k, "AXRole") == "AXTable"), None)
        if table is None:
            continue
        bar = next((k for k in kids if ax.attr(k, "AXRole") == "AXScrollBar"
                    and ax.attr(k, "AXOrientation") == "AXVerticalOrientation"), None)
        return e, table, bar
    raise KakaoError("메시지 목록을 찾지 못했습니다")


def composer(ax: AX, win):
    """입력창 — 스크롤 영역 바로 아래의 글 상자 (말풍선 글 상자는 칸 아래에 있다)."""
    found = [e for e in ax.walk(win) if ax.attr(e, "AXRole") == "AXTextArea"
             and ax.attr(ax.attr(e, "AXParent"), "AXRole") == "AXScrollArea"]
    if not found:
        raise KakaoError("입력창을 찾을 수 없습니다")
    return found[-1]


def send_button(ax: AX, win):
    return next((e for e in ax.walk(win) if ax.attr(e, "AXRole") == "AXButton"
                 and ax.attr(e, "AXTitle") == "전송"), None)


def context_menu(ax: AX, win, timeout: float = 2.5):
    """AXShowMenu 로 연 메뉴. **그 창 아래**에 붙는다 (앱 전체를 훑으면 수십 초)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        menu = next((e for e in ax.walk(win, max_depth=6) if ax.attr(e, "AXRole") == "AXMenu"), None)
        if menu is not None and ax.kids(menu):
            return menu
        time.sleep(0.15)
    return None


def scroll_into_view(ax: AX, win, cell):
    """대상 칸을 보이는 영역으로. 화면 밖 칸에는 메뉴가 열리지 않는다. 스크롤했으면 (바, 원래 값)."""
    area, table, bar = message_area(ax, win)
    view, box = rect(ax, area), rect(ax, cell)
    if view is None or box is None:
        return None
    if view[1] <= box[1] and box[1] + box[3] <= view[1] + view[3]:
        return None
    if bar is None:
        raise KakaoError("메시지 목록을 스크롤할 수 없습니다")
    tbl = rect(ax, table)
    span = tbl[3] - view[3] if tbl else 0
    if span <= 0:
        return None
    old = ax.attr(bar, "AXValue")
    offset = (box[1] - tbl[1]) - max(0.0, (view[3] - box[3]) / 2)
    ax.set_attr(bar, "AXValue", max(0.0, min(1.0, offset / span)))
    time.sleep(0.4)
    return bar, (old if old is not None else 1.0)


def restore_scroll(ax: AX, scrolled) -> None:
    """움직인 스크롤만 되돌린다. 새 메시지로 맨 아래로 내려갔으면 그대로 둔다."""
    if not scrolled:
        return
    bar, old = scrolled
    now = ax.attr(bar, "AXValue")
    if now is not None and abs(float(now) - 1.0) < 0.001:
        return
    ax.set_attr(bar, "AXValue", old)


# --- 읽기 ----------------------------------------------------------------------


@dataclass
class Message:
    text: str
    sender: str | None      # 받은 메시지의 보낸 사람 (이름표 없는 연속 메시지는 앞 사람을 물려받음)
    outgoing: bool          # 이 Mac 의 계정이 보낸 메시지
    time: str | None        # 묶음의 마지막 줄에만 붙는다
    thread_comment: bool    # 스레드 댓글을 '채팅방에 함께 보내기'로 올린 글
    system: bool            # 입퇴장 같은 시스템 공지


def _parse_cell(ax: AX, cell):
    """(본문, 이름표, 보낸 쪽, 시각, 함께 보낸 댓글) — 글 상자가 없으면 None."""
    kids = ax.kids(cell)
    area = next((k for k in kids if ax.attr(k, "AXRole") == "AXTextArea"), None)
    if area is None:
        return None
    text = ax.attr(area, "AXValue") or ""
    box, cbox = rect(ax, area), rect(ax, cell)
    label, stamp, head, link = None, None, None, False
    for k in kids:
        role = ax.attr(k, "AXRole")
        if role == "AXStaticText":
            r = rect(ax, k)
            if ax.attr(k, "AXHelp") is not None:              # 시각 라벨 (AXHelp 에 날짜)
                stamp = (ax.attr(k, "AXValue") or "").split("\n")[-1] or None
            else:
                label = ax.attr(k, "AXValue") or label
                if r and box and r[1] + r[3] <= box[1]:
                    head = max(head or 0, r[1] + r[3])
        elif role == "AXButton":
            r = rect(ax, k)
            if r and box and r[1] >= box[1] + box[3] - 1 and r[3] <= LINK_MAX_H:
                link = True
    outgoing, quote = False, False
    if box and cbox:
        left = box[0] - cbox[0]
        right = (cbox[0] + cbox[2]) - (box[0] + box[2])
        outgoing = right < left                  # 받은 글은 왼쪽, 보낸 글은 오른쪽 여백이 좁다
        top = head if head is not None else cbox[1]
        quote = box[1] - top >= QUOTE_GAP
    return text, label, outgoing, stamp, link and quote


def read_room(ax: AX, room: str, count: int) -> tuple[list[Message], bool]:
    """방 창 맨 아래 글 메시지 count 개 (오래된 것부터). 두 번째 값: 위로 스크롤돼 있는가."""
    win = ax.window(room)
    _, table, bar = message_area(ax, win)
    value = ax.attr(bar, "AXValue") if bar is not None else None
    scrolled_up = value is not None and float(value) < 0.999
    raw = []
    for row in reversed(ax.kids(table)):
        if len(raw) >= count:
            break
        if ax.attr(row, "AXRole") != "AXRow":
            continue
        cell = next(iter(ax.kids(row)), None)
        parsed = _parse_cell(ax, cell) if cell is not None else None
        if parsed is not None:
            raw.append(parsed)
    raw.reverse()
    out: list[Message] = []
    last: str | None = None
    for text, label, outgoing, stamp, thread_comment in raw:
        system = bool(SYSTEM_NOTICE.search(text))
        if outgoing or system:
            last, sender = None, None
        else:
            last = label or last
            sender = last
        out.append(Message(text, sender, outgoing and not system, stamp, thread_comment, system))
    return out, scrolled_up


@dataclass
class Notice:
    time: str
    parent: str     # 원글 앞부분 (길면 '…')
    latest: str     # 스레드 최신 댓글 앞부분 — 누가 썼는지는 알림에 없다
    unread: bool


def clean(text: str | None) -> str:
    return " ".join((text or "").replace(ZWSP, "").split())


def read_notices(ax: AX, room: str, top: int) -> list[Notice]:
    """'댓글 알림' 창 맨 위 top 줄 중 room 의 알림. 다른 방 줄은 방 이름만 보고 넘긴다."""
    win = ax.window(NOTICE_WINDOW)
    area = next((k for k in ax.kids(win) if ax.attr(k, "AXRole") == "AXScrollArea"), None)
    table = next((k for k in ax.kids(area) if ax.attr(k, "AXRole") == "AXTable"), None) if area else None
    if table is None:
        return []
    out = []
    rows = [r for r in ax.kids(table) if ax.attr(r, "AXRole") == "AXRow"]
    for row in rows[:top]:
        cell = next((c for c in ax.kids(row) if ax.attr(c, "AXRole") == "AXCell"), None)
        kids = ax.kids(cell) if cell is not None else []
        statics = [ax.attr(k, "AXValue") for k in kids if ax.attr(k, "AXRole") == "AXStaticText"]
        if len(statics) < 2 or clean(statics[1]) != room:
            continue                                          # 다른 방 — 내용은 읽지 않는다
        areas = [ax.attr(a, "AXValue") for k in kids if ax.attr(k, "AXRole") == "AXScrollArea"
                 for a in ax.kids(k) if ax.attr(a, "AXRole") == "AXTextArea"]
        if len(areas) < 2:
            continue
        unread = any(ax.attr(k, "AXRole") == "AXImage" and ax.attr(k, "AXDescription") == UNREAD_ICON
                     for k in kids)
        out.append(Notice(clean(statics[0]), clean(areas[0]), clean(areas[1]), unread))
    return out


# --- 스레드(댓글) ----------------------------------------------------------------
# 원글 칸 AXShowMenu → 메뉴 '댓글' AXPick → 새로 열린 스레드 창. 커서가 움직이지 않는다.
# 카카오톡의 AX 액션 목록은 믿을 수 없다: '댓글' 항목은 AXPick 만 되고, '전송' 버튼은 목록에
# 없지만 AXPress 가 된다. 그래서 매번 결과를 다시 읽어 확인한다.


@dataclass
class ThreadMessage:
    text: str
    outgoing: bool
    sender: str | None


def _thread_windows(ax: AX) -> list:
    """스레드 창 — 제목이 비어 있고 '채팅방에 함께 보내기' 체크박스가 있는 창."""
    return [w for w in ax.windows() if not ax.attr(w, "AXTitle")
            and any(ax.attr(e, "AXTitle") == MIRROR_CHECKBOX for e in ax.walk(w))]


def _aux_windows(ax: AX) -> list:
    return [w for w in ax.windows() if ax.attr(w, "AXTitle") == AUX_WINDOW]


def _is_new(window, before: list) -> bool:
    return not any(window == b for b in before)       # AX 요소는 == 로 같은 창인지 비교된다


@dataclass
class Opened:
    """이 명령이 연 스레드 창. 닫을 때 **이 명령이 연 창만** 닫는다."""
    window: object
    scrolled: tuple | None
    aux_before: list


def _close_new_aux(ax: AX, aux_before: list) -> None:
    for w in _aux_windows(ax):
        if _is_new(w, aux_before):
            b = ax.attr(w, "AXCloseButton")
            if b is not None:
                ax.perform(b, "AXPress")


def close_thread(ax: AX, opened: Opened) -> None:
    restore_scroll(ax, opened.scrolled)
    btn = ax.attr(opened.window, "AXCloseButton")
    if btn is not None:
        ax.perform(btn, "AXPress")
    _close_new_aux(ax, opened.aux_before)


def open_thread(ax: AX, room: str, target: str, sender: str | None = None, nth: int = 0) -> Opened:
    """원글 칸 → 메뉴 '댓글' → 새로 열린 스레드 창. 같은 본문이 여럿이면 최근에서 nth 번째.

    **새로 열린 스레드 창만** 쓴다 — 사람이 열어 둔 스레드 창에는 쓰지도 닫지도 않는다.
    같은 스레드를 사람이 이미 열어 두었으면 새 창이 안 생기므로 멈춘다.
    """
    win = ax.window(room, need="visible")
    threads_before, aux_before = _thread_windows(ax), _aux_windows(ax)

    def cells():
        found = [e for e in ax.walk(win) if ax.attr(e, "AXRole") == "AXCell"
                 and any(ax.attr(c, "AXValue") == target for c in ax.kids(e))]
        if sender:
            named = [e for e in found if any(
                ax.attr(c, "AXRole") == "AXStaticText" and ax.attr(c, "AXValue") == sender
                and ax.attr(c, "AXHelp") is None for c in ax.kids(e))]
            found = named or found          # 연속 메시지는 이름표가 없어 못 찾으면 원래 목록
        return found

    found = cells()
    if len(found) <= nth:
        raise KakaoError(f"메시지를 찾을 수 없습니다: {target!r} (최근 {nth + 1}번째)")
    cell = found[-1 - nth]
    scrolled = scroll_into_view(ax, win, cell)
    try:
        if scrolled:
            found = cells()
            cell = found[-1 - nth] if len(found) > nth else None
            view = rect(ax, message_area(ax, win)[0])
            box = rect(ax, cell) if cell is not None else None
            if box is None or not (view[1] <= box[1] and box[1] + box[3] <= view[1] + view[3]):
                raise KakaoError("메시지를 화면 안으로 가져오지 못했습니다")
        if ax.perform(cell, "AXShowMenu") != 0:
            raise KakaoError("메뉴를 열지 못했습니다")
        menu = context_menu(ax, win)
        if menu is None:
            raise KakaoError("메뉴가 열리지 않았습니다")
        item = next((e for e in ax.kids(menu) if ax.attr(e, "AXRole") == "AXMenuItem"
                     and ax.attr(e, "AXTitle") == "댓글"), None)
        if item is None:
            ax.perform(menu, "AXCancel")
            raise KakaoError("메뉴에 '댓글'이 없습니다 (함께 보낸 댓글에는 다시 댓글을 달 수 없습니다)")
        ax.perform(item, "AXPick")
        thread = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and thread is None:
            time.sleep(0.3)
            thread = next((w for w in _thread_windows(ax) if _is_new(w, threads_before)), None)
        if thread is None:
            raise KakaoError("스레드 창이 새로 열리지 않았습니다 (같은 스레드 창이 이미 열려 있을 수 있음)")
    except KakaoError:
        _close_new_aux(ax, aux_before)
        restore_scroll(ax, scrolled)
        raise
    return Opened(thread, scrolled, aux_before)


def parse_thread(ax: AX, thread) -> list[ThreadMessage]:
    """스레드 창의 말풍선들 (첫째가 원글). 받은/보낸은 방 화면과 같은 여백 규칙으로 가린다."""
    table = next((e for e in ax.walk(thread, max_depth=8) if ax.attr(e, "AXRole") == "AXTable"), None)
    if table is None:
        return []
    out: list[ThreadMessage] = []
    last: str | None = None
    for cell in ax.walk(table, max_depth=3):
        if ax.attr(cell, "AXRole") != "AXCell":
            continue
        area = next((e for e in ax.walk(cell, max_depth=4) if ax.attr(e, "AXRole") == "AXTextArea"), None)
        if area is None:
            continue                                   # '댓글 N개' 구분 줄, 버튼 칸
        cb, tb = rect(ax, cell), rect(ax, area)
        outgoing = False
        if cb and tb:
            outgoing = (cb[0] + cb[2]) - (tb[0] + tb[2]) < tb[0] - cb[0]
        label = next((ax.attr(k, "AXValue") for k in ax.kids(cell)
                      if ax.attr(k, "AXRole") == "AXStaticText" and ax.attr(k, "AXHelp") is None
                      and ax.attr(k, "AXValue")), None)
        if outgoing:
            sender, last = None, None
        else:
            last = label or last
            sender = last
        out.append(ThreadMessage(ax.attr(area, "AXValue") or "", outgoing, sender))
    return out


def read_thread(ax: AX, room: str, parent: str, sender: str | None = None, nth: int = 0):
    opened = open_thread(ax, room, parent, sender, nth)
    try:
        messages = parse_thread(ax, opened.window)
        if not messages or messages[0].text != parent:
            raise KakaoError(f"엉뚱한 스레드가 열렸습니다: {messages[:1]!r}")
        return messages
    finally:
        close_thread(ax, opened)


# --- 사진 글자 -------------------------------------------------------------------


def _cells(ax: AX, win) -> list:
    """메시지 표의 칸 전부 (날짜 줄·사진 줄까지) — 사진 줄이 잠깐 비어도 거리가 흔들리지 않게."""
    table = next((e for e in ax.walk(win, max_depth=8) if ax.attr(e, "AXRole") == "AXTable"), None)
    return [] if table is None else [c for c in ax.walk(table, max_depth=3) if ax.attr(c, "AXRole") == "AXCell"]


def _cell_text(ax: AX, cell) -> str | None:
    area = next((e for e in ax.walk(cell, max_depth=6) if ax.attr(e, "AXRole") == "AXTextArea"), None)
    return ax.attr(area, "AXValue") if area is not None else None


def _is_photo(ax: AX, cell) -> bool:
    if _cell_text(ax, cell) is not None:
        return False
    best = 0.0
    for e in ax.walk(cell, max_depth=6):
        if ax.attr(e, "AXRole") == "AXImage":
            r = rect(ax, e)
            if r:
                best = max(best, r[2], r[3])
    return best >= MIN_PHOTO_PX


def save_photo(ax: AX, room: str, near: str, offset: int, save_dir: Path, timeout: float = 8.0) -> Path:
    """near 말풍선에서 offset 줄 떨어진 사진을 '저장하기'로 받는다. 연 메뉴는 닫고 스크롤은 되돌린다."""
    win = ax.window(room, need="visible")

    def target():
        rows = _cells(ax, win)
        q = next((i for i in range(len(rows) - 1, -1, -1) if _cell_text(ax, rows[i]) == near), None)
        if q is None:
            raise KakaoError(f"메시지를 찾을 수 없습니다: {near!r}")
        t = q + offset
        return rows[t] if 0 <= t < len(rows) and _is_photo(ax, rows[t]) else None

    cell = target()
    for _ in range(2):
        if cell is not None:
            break
        time.sleep(1.0)               # 사진 줄은 그림을 다시 그리는 3초가량 비기도 한다
        cell = target()
    if cell is None:
        raise KakaoError(f"그 자리({offset:+d}줄)에 사진이 없습니다")

    before = set(save_dir.glob(PHOTO_GLOB)) if save_dir.is_dir() else set()
    scrolled, menu = None, None
    try:
        scrolled = scroll_into_view(ax, win, cell)
        if scrolled:
            cell = target()
            if cell is None:
                raise KakaoError("사진을 화면 안으로 가져오지 못했습니다")
        if ax.perform(cell, "AXShowMenu") != 0:
            raise KakaoError("메뉴를 열지 못했습니다")
        menu = context_menu(ax, win)
        if menu is None:
            raise KakaoError("메뉴가 열리지 않았습니다")
        item = next((e for e in ax.walk(menu, max_depth=3) if ax.attr(e, "AXRole") == "AXMenuItem"
                     and ax.attr(e, "AXTitle") == SAVE_ITEM), None)
        if item is None:
            raise KakaoError(f"메뉴에 '{SAVE_ITEM}'가 없습니다 (이미 저장한 사진은 '폴더열기'가 뜹니다)")
        ax.perform(item, "AXPick")
        menu = None                                     # 고르면 메뉴는 스스로 닫힌다
        deadline, candidate, last_size = time.monotonic() + timeout, None, -1
        while time.monotonic() < deadline:
            fresh = [p for p in (set(save_dir.glob(PHOTO_GLOB)) - before) if p.suffix.lower() in IMAGE_SUFFIXES]
            if fresh:
                newest = max(fresh, key=lambda p: p.stat().st_mtime)
                size = newest.stat().st_size
                if newest == candidate and size == last_size and size > 0:
                    return newest                      # 크기가 두 번 같으면 다 쓰인 것
                candidate, last_size = newest, size
            time.sleep(0.2)
        raise KakaoError(f"{timeout:.0f}초 안에 저장된 파일이 {save_dir} 에 생기지 않았습니다")
    finally:
        if menu is not None:
            ax.perform(menu, "AXCancel")
        leftover = context_menu(ax, win, timeout=0.3)
        if leftover is not None:
            ax.perform(leftover, "AXCancel")
        restore_scroll(ax, scrolled)


def ocr(path: Path) -> str:
    """그림 속 글자 (맥 내장 Vision, 네트워크 안 씀)."""
    import Quartz
    import Vision

    url = Quartz.CFURLCreateWithFileSystemPath(None, str(path), Quartz.kCFURLPOSIXPathStyle, False)
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None or Quartz.CGImageSourceGetCount(source) == 0:
        raise KakaoError(f"그림으로 열 수 없습니다: {path}")
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(list(OCR_LANGUAGES))
    request.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, {})
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        raise KakaoError(f"글자 인식 실패: {err}")
    lines: list[str] = []
    for obs in request.results() or []:
        cand = obs.topCandidates_(1)
        if cand and cand[0].confidence() >= OCR_MIN_CONFIDENCE:
            s = cand[0].string().strip()
            if s and not (lines and lines[-1] == s):
                lines.append(s)
    return "\n".join(lines)


# --- 보내기 ----------------------------------------------------------------------


def allowed_rooms(path: Path = ALLOWLIST) -> list[str]:
    """보내도 되는 방 (정확한 이름). 못 읽으면 빈 목록 — 아무 데도 보내지 않는다."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return [str(c) for c in data.get("safety", {}).get("allowed_send_chats", [])]


def confirm_before_send(path: Path = SETTINGS) -> bool:
    """보내기 전에 미리보기·확인을 거치나. 설정이 없거나 못 읽으면 거친다 (기본)."""
    try:
        with path.open("rb") as fh:
            return bool(tomllib.load(fh).get("send", {}).get("confirm", True))
    except (OSError, tomllib.TOMLDecodeError):
        return True


def set_confirm(on: bool, path: Path = SETTINGS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# kakaotalk 스킬 설정 — `kakaotalk.py set-confirm on|off` 로 바꾼다\n"
        "[send]\n"
        "# true: 보내기 전에 미리보기를 보여 주고 확인받는다 / false: 허용 목록의 방이면 바로 보낸다\n"
        f"confirm = {'true' if on else 'false'}\n",
        encoding="utf-8",
    )


def check_allowed(room: str) -> None:
    if room not in allowed_rooms():
        raise KakaoError(
            f"'{room}' 은 전송 허용 목록에 없습니다 — {ALLOWLIST} 의 [safety] allowed_send_chats 에 "
            "방 창 제목을 정확히 넣어야 보냅니다 (나와의 채팅은 본인 이름 — `windows` 로 확인)"
        )


def bot_running() -> bool:
    """kakaobot 봇이 돌고 있으면 화면 조작이 겹칠 수 있다 (스레드 창을 서로 잡는 등)."""
    try:
        out = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any("-m kakaobot" in line and "kakaobot.menubar" not in line for line in out.splitlines())


# --- 처음 쓰는 사람 안내 ----------------------------------------------------------


@dataclass
class Check:
    ok: bool | None         # True 됨 · False 안 됨(필수) · None 선택·참고
    item: str
    detail: str
    fix: str = ""


def _host_app() -> str:
    """이 명령을 띄운 앱 이름 (손쉬운 사용 권한을 켜야 하는 앱)."""
    import os

    bundle = os.environ.get("__CFBundleIdentifier", "")
    try:
        from AppKit import NSWorkspace

        url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(bundle) if bundle else None
        if url is not None:
            return Path(url.path()).stem
    except Exception:  # pragma: no cover
        pass
    return bundle or "이 명령을 실행한 앱(터미널 등)"


def _kakao_version() -> tuple[bool, str | None]:
    """(실행 중인가, 버전). 설치돼 있지 않으면 (False, None)."""
    try:
        from AppKit import NSWorkspace
        from Foundation import NSBundle

        ws = NSWorkspace.sharedWorkspace()
        app = next((a for a in ws.runningApplications() if a.bundleIdentifier() == KAKAO_BUNDLE), None)
        url = app.bundleURL() if app is not None else ws.URLForApplicationWithBundleIdentifier_(KAKAO_BUNDLE)
        if url is None:
            return False, None
        info = NSBundle.bundleWithURL_(url).infoDictionary() or {}
        return app is not None, str(info.get("CFBundleShortVersionString") or "?")
    except Exception:  # pragma: no cover
        return False, None


def guide_checks() -> list[Check]:
    """처음 쓸 때 필요한 것을 차례로 점검한다. 손쉬운 사용 권한이 없어도 돈다 (읽기·보내기는 하지 않는다)."""
    import platform

    from ApplicationServices import AXIsProcessTrusted

    out = [Check(True, "macOS", platform.mac_ver()[0] or "?")]
    running, ver = _kakao_version()
    if ver is None:
        out.append(Check(False, "카카오톡", "설치돼 있지 않습니다", "App Store 에서 카카오톡을 설치하고 로그인하세요"))
    elif not running:
        out.append(Check(False, "카카오톡", f"{ver} — 실행 중이 아닙니다", "카카오톡을 켜고 로그인하세요"))
    else:
        tested = ver.startswith(("25.9", "26.8", "26.9"))
        out.append(Check(True if tested else None, "카카오톡", ver + ("" if tested else
                         " — 실측한 판(25.9·26.8·26.9)과 달라 일부 동작이 다를 수 있습니다")))
    host = _host_app()
    trusted = bool(AXIsProcessTrusted())
    out.append(Check(trusted, "손쉬운 사용 권한", f"{host}: " + ("켜져 있음" if trusted else "꺼져 있음"),
                     "" if trusted else
                     f"시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용 에서 '{host}' 을 켜고, {host} 를 완전히 "
                     "종료했다가 다시 여세요 (목록에 없으면 + 로 추가)"))
    if trusted and running:
        try:
            ax = AX()
            titles = [t for t in (ax.attr(w, "AXTitle") for w, _ in ax.all_windows()) if t]
            out.append(Check(True if titles else False, "열린 대화방 창", f"{len(titles)}개",
                             "" if titles else "카카오톡 목록에서 쓸 방을 더블클릭해 별도 창으로 여세요"))
        except KakaoError as exc:
            out.append(Check(False, "열린 대화방 창", str(exc)))
    if not ALLOWLIST.exists():
        out.append(Check(None, "전송 허용 목록", f"{str(ALLOWLIST).replace(str(Path.home()), '~', 1)} 없음 — 읽기만 됩니다",
                         "보내기를 쓰려면 보내도 되는 방을 알려 주세요. 파일을 만들어 그 방만 넣습니다"))
    else:
        rooms = allowed_rooms()
        out.append(Check(True if rooms else None, "전송 허용 목록",
                         f"{len(rooms)}개 방" if rooms else "비어 있음 — 읽기만 됩니다",
                         "" if rooms else "보내도 되는 방을 알려 주세요"))
    out.append(Check(None, "보내기 확인", "미리보기를 보고 확인한 뒤 보냄 (기본)" if confirm_before_send()
                     else "미리보기 없이 바로 보냄 — 허용 목록의 방만"))
    if bot_running():
        out.append(Check(None, "kakaobot", "돌고 있음 — 화면 조작·보내기 명령은 봇을 멈춘 뒤에 됩니다"))
    return out


USAGE = """\
할 수 있는 것 (Claude 에게 이렇게 말하면 됩니다)
  읽기 — 창을 건드리지 않습니다
    "카톡 창 뭐 열려 있어?"                → 열린 방 창 제목
    "○○ 방 최근 20개 읽어줘"               → 최근 메시지 (보낸 사람·시각·스레드 댓글 구분)
    "○○ 방 댓글 알림 확인해줘"             → '댓글 알림' 창에서 그 방 알림
  화면 조작 — 방 창이 화면에 보여야 합니다 (메뉴·스레드 창을 잠깐 열고 닫음)
    "○○ 방 '원글' 스레드 읽어줘"            → 댓글까지
    "○○ 방 '메시지' 위 사진 글자 읽어줘"     → 사진 속 글자 (받은 사진은 읽고 바로 지움)
  보내기 — 허용 목록의 방만. 기본은 미리보기를 보고 '보내'라고 해야 나가고, "바로 보내게 해줘"라고 하면 확인 없이 나갑니다
    "○○ 방에 '안녕하세요' 보내줘"
    "○○ 방 '원글'에 '확인했습니다' 댓글 달아줘"
    "이 사진 ○○ 방에 보내줘"

처음 해 보기 (안전한 순서)
  1. 카카오톡에서 방 하나를 더블클릭해 별도 창으로 연다
  2. "카톡 창 뭐 열려 있어?" → "그 방 최근 5개 읽어줘"
  3. 보내기는 '나와의 채팅'으로 시험 — 카카오톡 26.8 은 그 창 제목이 본인 이름이라 그 이름을 허용 목록에 넣는다

지키는 것: 이름을 댄 방만 읽고, 창을 앞으로 가져오지 않고, 화면 캡처·클립보드를 쓰지 않습니다.
"""


def _bubbles(ax: AX, win) -> list[tuple[str, bool]]:
    """(본문, 보낸 것인가) — 방 창 맨 아래 30줄."""
    _, table, _ = message_area(ax, win)
    out = []
    for row in list(ax.kids(table))[-30:]:
        cell = next(iter(ax.kids(row)), None)
        parsed = _parse_cell(ax, cell) if cell is not None else None
        if parsed is not None:
            out.append((parsed[0], parsed[2]))
    return out


def _focus(ax: AX, win, field) -> None:
    """방 창을 카카오톡 안에서 올리고(AXRaise — 앱을 앞으로 가져오지 않는다) 입력창에 포커스."""
    ax.perform(win, "AXRaise")
    time.sleep(0.3)
    for _ in range(3):
        ax.set_attr(field, "AXFocused", True)
        time.sleep(0.2)
        if ax.attr(field, "AXFocused"):
            return
    raise KakaoError("입력창에 포커스를 주지 못했습니다")


def send_plain(ax: AX, room: str, text: str) -> str:
    """일반 메시지. 입력창에 글을 넣고 보낸 뒤 **새 말풍선이 생겼는지 확인**한다.

    '전송' 버튼이 있으면(26.8) 창을 올리지도 포커스를 주지도 않고, 입력창 값만 넣어 버튼을 누른다 —
    최소화한 창에도 된다 (실측). 버튼이 없는 판(25.9)은 창을 카카오톡 안에서 올리고 입력창에 포커스를
    준 뒤 Return 을 보낸다 (그때는 창이 화면에 보여야 한다).
    입력창에 쓰던 글이 있으면 덮어쓰지 않고 멈춘다. 클립보드는 쓰지 않는다.
    """
    win = ax.window(room, need="listed")
    field = composer(ax, win)
    if ax.attr(field, "AXValue"):
        raise KakaoError("입력창에 작성 중인 글이 있어 덮어쓰지 않습니다")
    button = send_button(ax, win)
    if button is None and ax.attr(win, "AXMinimized"):
        raise KakaoError("이 카카오톡은 '전송' 버튼이 없어 창이 화면에 보여야 보냅니다 — 최소화를 풀어 주세요")
    before = _bubbles(ax, win).count((text, True))
    if button is None:
        _focus(ax, win, field)
    ax.set_attr(field, "AXValue", text)
    time.sleep(0.3)
    if ax.attr(field, "AXValue") != text:
        ax.set_attr(field, "AXValue", "")
        raise KakaoError("입력창에 글을 넣지 못했습니다")
    if button is not None:
        if not _wait(lambda: ax.attr(button, "AXEnabled"), 3.0):
            ax.set_attr(field, "AXValue", "")
            raise KakaoError("'전송' 버튼이 켜지지 않았습니다 — 보내지 않았습니다")
        ax.perform(button, "AXPress")
        how = "전송 버튼"
    else:
        _focus(ax, win, field)                     # 누르기 직전에 포커스를 다시 확인
        if ax.attr(field, "AXValue") != text:
            raise KakaoError("보내기 직전에 입력창 내용이 바뀌었습니다 — 보내지 않았습니다")
        ax.press_return()
        how = "Return"
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if _bubbles(ax, win).count((text, True)) > before:
            return f"전송 확인 ({how})"
    left = ax.attr(field, "AXValue") == text
    if left:
        ax.set_attr(field, "AXValue", "")              # 안 나간 글을 입력창에 남겨 두지 않는다
    raise Uncertain("보냈는지 확인하지 못했습니다 — 다시 보내지 말고 방을 확인하세요"
                    + (" (입력창에 글이 그대로 남아 있어 지웠습니다 — 안 나갔을 가능성이 큽니다)" if left else ""))


def reply_in_thread(ax: AX, room: str, target: str, text: str, mirror: bool = True,
                    sender: str | None = None, nth: int = 0) -> str:
    """target 메시지에 댓글로. 스레드 창을 새로 열어 쓰고 닫는다."""
    opened = open_thread(ax, room, target, sender, nth)
    thread = opened.window
    try:
        texts = [m.text for m in parse_thread(ax, thread)]
        if not texts or texts[0] != target:
            raise KakaoError(f"엉뚱한 스레드가 열렸습니다: {texts[:1]!r}")
        cb = next((e for e in ax.walk(thread) if ax.attr(e, "AXRole") == "AXCheckBox"), None)
        if cb is None:
            raise KakaoError(f"'{MIRROR_CHECKBOX}' 체크박스를 찾을 수 없습니다")
        want = 1 if mirror else 0
        if ax.attr(cb, "AXValue") != want:
            ax.perform(cb, "AXPress")
            time.sleep(0.3)
        if ax.attr(cb, "AXValue") != want:
            raise KakaoError("'채팅방에 함께 보내기'를 바꾸지 못했습니다")
        field = composer(ax, thread)
        if ax.attr(field, "AXValue"):
            raise KakaoError("스레드 입력창에 작성 중인 글이 있어 덮어쓰지 않습니다")
        ax.set_attr(field, "AXValue", text)
        time.sleep(0.3)
        if ax.attr(field, "AXValue") != text:
            raise KakaoError("스레드 입력창에 글을 넣지 못했습니다")
        button = send_button(ax, thread)
        if button is None:
            ax.set_attr(field, "AXValue", "")
            raise KakaoError("'전송' 버튼을 찾을 수 없습니다")
        before = len(parse_thread(ax, thread))
        ax.perform(button, "AXPress")                   # 여기부터는 나갔을 수 있다
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.4)
            after = parse_thread(ax, thread)
            if len(after) > before and after[-1].text == text:
                return "댓글 전송 확인" + (" (방에도 함께)" if mirror else "")
        raise Uncertain("댓글을 보냈는지 확인하지 못했습니다 — 다시 보내지 말고 스레드를 확인하세요")
    finally:
        close_thread(ax, opened)


# --- 사진·파일 보내기 -------------------------------------------------------------
# 방 창 '파일전송(⌘O)' → macOS 파일 선택 창(시트) → 왼쪽 '다운로드' → 임시 폴더 → 파일 → '열기'
# → 카카오톡 미리보기 시트('파일 전송' · 파일 이름 · 크기) → '1개 전송'. 클립보드·포커스를 쓰지 않는다.
# 파일 선택 창에는 경로를 바로 넣는 칸이 접근성으로 보이지 않는다. 그래서 보낼 파일을 다운로드 안
# 새 임시 폴더에 복제해 두고 그 폴더를 고른다 — 폴더에 그 파일 하나뿐이라 엉뚱한 파일을 고를 수 없다.
# 마지막 '1개 전송' 전까지는 언제든 '취소'로 되돌린다. (카카오톡 26.8 실측)

FILE_BUTTON_SHORTCUT = "⌘O"                 # '파일전송' 버튼 AXHelp 끝
SIDEBAR_DOWNLOADS = ("다운로드", "Downloads")
STAGE_PREFIX = "kakaotalk-send-"
STAGE_NAME = re.compile(r"^kakaotalk-send-[0-9a-f]{8}$")
STAGE_MAX_AGE = 3600                         # 이보다 오래된 임시 폴더는 중간에 멈춘 흔적 — 다음 실행 때 지운다
COMMIT_TITLES = ("1개 전송", "Send 1 files")  # 미리보기의 보내기 버튼
CANCEL_TITLES = ("취소", "Cancel")
PICKER_TIMEOUT = 15.0                        # 파일 선택 창은 뜨는 데 4초쯤 걸린다
READY_TIMEOUT = 30.0                         # 큰 파일은 '열기'·미리보기가 늦다
VERIFY_TIMEOUT = 30.0
UPLOAD_TIMEOUT = 120.0


def _nfc(text) -> str:
    return unicodedata.normalize("NFC", str(text or ""))


def _wait(pred, timeout: float, step: float = 0.15):
    deadline = time.monotonic() + timeout
    while True:
        value = pred()
        if value or time.monotonic() >= deadline:
            return value
        time.sleep(step)


def _sheet(ax: AX, win):
    """창에 붙은 시트 (파일 선택 창·미리보기)."""
    return next((e for e in ax.walk(win, max_depth=3) if ax.attr(e, "AXRole") == "AXSheet"), None)


def _label(ax: AX, e) -> str | None:
    for x in ax.walk(e, max_depth=4):
        if ax.attr(x, "AXRole") in ("AXTextField", "AXStaticText") and ax.attr(x, "AXValue"):
            return _nfc(ax.attr(x, "AXValue"))
    return None


def _cancel_sheet(ax: AX, win) -> None:
    """열려 있는 시트를 '취소'로 닫는다. 보내는 버튼은 누르지 않는다."""
    sheet = _sheet(ax, win)
    if sheet is None:
        return
    cancels = [e for e in ax.walk(sheet, max_depth=16) if ax.attr(e, "AXRole") == "AXButton"
               and (ax.attr(e, "AXTitle") in CANCEL_TITLES or ax.attr(e, "AXIdentifier") == "CancelButton")]
    if len(cancels) == 1:
        ax.perform(cancels[0], "AXPress")
    else:
        ax.perform(sheet, "AXCancel")
    _wait(lambda: _sheet(ax, win) is None, 4.0)


def _stage(src: Path, downloads: Path) -> Path:
    """다운로드 안 새 임시 폴더에 src 를 같은 이름으로 복제한다 (APFS 복제라 공간을 안 쓴다)."""
    for old in downloads.glob(STAGE_PREFIX + "*"):
        try:
            if (STAGE_NAME.match(old.name) and old.is_dir() and len(list(old.iterdir())) <= 1
                    and time.time() - old.stat().st_mtime > STAGE_MAX_AGE):
                shutil.rmtree(old)
        except OSError:
            pass
    folder = downloads / f"{STAGE_PREFIX}{secrets.token_hex(4)}"
    folder.mkdir()
    dst = folder / src.name
    if subprocess.run(["/bin/cp", "-c", str(src), str(dst)], capture_output=True).returncode != 0:
        shutil.copy2(src, dst)                      # 다른 디스크면 그냥 복사
    return dst


def _unstage(staged: Path) -> None:
    folder = staged.parent
    if STAGE_NAME.match(folder.name):
        shutil.rmtree(folder, ignore_errors=True)


def _columns(ax: AX, sheet) -> list:
    browser = next((e for e in ax.walk(sheet, max_depth=12) if ax.attr(e, "AXRole") == "AXBrowser"), None)
    if browser is None:
        raise KakaoError("파일 선택 창이 '열' 보기가 아닙니다 — 카카오톡 파일전송 창 위쪽 보기 단추에서 '열'로 "
                         "바꿔 주세요")
    return [e for e in ax.walk(browser, max_depth=8) if ax.attr(e, "AXRole") == "AXList"]


def _chosen(ax: AX, column, item) -> bool:
    sel = ax.attr(column, "AXSelectedChildren")
    return sel is not None and len(sel) == 1 and sel[0] == item


def _choose(ax: AX, column, item) -> bool:
    """열 목록에서 항목 하나를 고른다. 항목 쪽 AXSelected 는 못 바꿔 목록 쪽을 바꾼다.
    결과 코드는 오류여도 먹힐 때가 있어 믿지 않고 다시 읽어 확인한다."""
    for _ in range(2):
        if _chosen(ax, column, item):
            return True
        ax.set_attr(column, "AXSelectedChildren", [item])
        if _wait(lambda: _chosen(ax, column, item), 3.0):
            return True
    return False


def _pick(ax: AX, sheet, staged: Path) -> None:
    """파일 선택 창에서 staged 를 고르고 '열기'를 누른다 — 아직 보내지 않는다."""
    outline = next((e for e in ax.walk(sheet, max_depth=12) if ax.attr(e, "AXRole") == "AXOutline"), None)
    side = next((r for r in (ax.attr(outline, "AXRows") or []) if _label(ax, r) in SIDEBAR_DOWNLOADS),
                None) if outline is not None else None
    if side is None:
        raise KakaoError("파일 선택 창 왼쪽 목록에 '다운로드'가 없습니다 — Finder 설정 > 사이드바에서 "
                         "'다운로드'를 켜 주세요")
    ax.set_attr(side, "AXSelected", True)

    def folder():
        for i, column in enumerate(_columns(ax, sheet)):
            item = next((k for k in ax.kids(column) if _label(ax, k) == staged.parent.name), None)
            if item is not None:
                return i, column, item
        return None

    found = _wait(folder, PICKER_TIMEOUT)
    if not found:
        raise KakaoError("파일 선택 창의 다운로드 폴더에서 임시 폴더가 보이지 않습니다")
    index, column, item = found
    if not _choose(ax, column, item):
        raise KakaoError("파일 선택 창에서 임시 폴더를 고르지 못했습니다")
    names = {_nfc(staged.name), _nfc(staged.stem)}     # 확장자를 숨기면 이름만 보인다

    def inside():
        for column in _columns(ax, sheet)[index + 1:]:
            kids = ax.kids(column)
            if len(kids) == 1 and _label(ax, kids[0]) in names:
                return column, kids[0]
        return None

    found = _wait(inside, PICKER_TIMEOUT)
    if not found:
        raise KakaoError("임시 폴더 안에 보낼 파일 하나만 보여야 하는데 그렇지 않습니다")
    if not _choose(ax, *found):
        raise KakaoError("파일 선택 창에서 파일을 고르지 못했습니다")
    ok = next((e for e in ax.walk(sheet, max_depth=12) if ax.attr(e, "AXIdentifier") == "OKButton"), None)
    if ok is None or not _wait(lambda: ax.attr(ok, "AXEnabled"), READY_TIMEOUT):
        raise KakaoError("파일 선택 창의 '열기' 버튼이 켜지지 않았습니다")
    ax.perform(ok, "AXPress")


def _commit_button(ax: AX, win, picker, staged: Path):
    """'열기' 뒤 카카오톡 미리보기 — 보낼 파일 이름이 정확히 한 번, 켜진 '1개 전송'이 하나일 때만 그 버튼."""
    names = {_nfc(staged.name), _nfc(staged.stem)}

    def ready():
        sheet = _sheet(ax, win)
        if sheet is None or sheet == picker:
            return None
        shown = [_nfc(ax.attr(e, "AXValue")) for e in ax.walk(sheet, max_depth=16)
                 if ax.attr(e, "AXRole") in ("AXStaticText", "AXTextField")]
        buttons = [e for e in ax.walk(sheet, max_depth=16) if ax.attr(e, "AXRole") == "AXButton"
                   and ax.attr(e, "AXTitle") in COMMIT_TITLES and ax.attr(e, "AXEnabled")]
        if sum(1 for v in shown if v in names) == 1 and len(buttons) == 1:
            return buttons[0]
        return None

    return _wait(ready, READY_TIMEOUT, 0.2)


def _rows(ax: AX, win) -> list:
    _, table, _ = message_area(ax, win)
    return [r for r in ax.kids(table) if ax.attr(r, "AXRole") == "AXRow"]


def _outgoing_attachment(ax: AX, row, names: set[str]) -> bool:
    """보낸 사진·파일 말풍선인가 — 글 상자가 없거나 글이 파일 이름뿐이고, 내용이 오른쪽에 붙은 칸."""
    cell = next(iter(ax.kids(row)), None)
    if cell is None:
        return False
    texts = [_nfc(ax.attr(e, "AXValue")) for e in ax.walk(cell, max_depth=6) if ax.attr(e, "AXRole") == "AXTextArea"]
    if any(t not in names for t in texts):
        return False                                  # 글 말풍선
    boxes = [rect(ax, e) for e in ax.walk(cell, max_depth=6)
             if ax.attr(e, "AXRole") in ("AXImage", "AXButton", "AXTextArea")
             or (ax.attr(e, "AXRole") == "AXStaticText" and ax.attr(e, "AXHelp") is None)]
    boxes = [b for b in boxes if b and b[2] > 0 and b[3] > 0]
    cbox = rect(ax, cell)
    if not boxes or cbox is None:
        return False
    left = min(b[0] for b in boxes) - cbox[0]
    right = (cbox[0] + cbox[2]) - max(b[0] + b[2] for b in boxes)
    return right < left


def _uploading(ax: AX, row) -> bool:
    return any(ax.attr(e, "AXRole") in ("AXProgressIndicator", "AXBusyIndicator") for e in ax.walk(row, max_depth=8))


def send_file(ax: AX, room: str, path: Path, downloads: Path | None = None) -> str:
    """사진·파일 하나. 보낸 뒤 **새로 생긴 보낸 사진·파일 말풍선**을 확인한다.
    사진은 사진 말풍선으로 나간다 (파일 선택 창으로 보내도 그렇다 — 26.8 실측)."""
    win = ax.window(room, need="visible")
    if _sheet(ax, win) is not None:
        raise KakaoError("방 창에 다른 시트(대화상자)가 열려 있습니다 — 닫은 뒤 다시 하세요")
    field = composer(ax, win)
    if ax.attr(field, "AXValue"):
        raise KakaoError("입력창에 작성 중인 글이 있어 멈춥니다 (파일과 함께 나갈 수 있어서)")
    buttons = [e for e in ax.walk(win) if ax.attr(e, "AXRole") == "AXButton"
               and (ax.attr(e, "AXHelp") or "").endswith(FILE_BUTTON_SHORTCUT)]
    if len(buttons) != 1:
        raise KakaoError("'파일전송(⌘O)' 버튼을 찾을 수 없습니다")
    staged = _stage(path, downloads or Path.home() / "Downloads")
    keep = False            # 보낸 뒤 올리기가 끝나기 전에는 카카오톡이 임시 파일을 읽을 수 있어 남긴다
    try:
        ax.perform(buttons[0], "AXPress")
        picker = _wait(lambda: _sheet(ax, win), PICKER_TIMEOUT)
        if picker is None:
            raise KakaoError("파일 선택 창이 열리지 않았습니다")
        _pick(ax, picker, staged)
        button = _commit_button(ax, win, picker, staged)
        if button is None:
            raise KakaoError("카카오톡 미리보기에 보낼 파일이 한 개로 보이지 않습니다")
        before = len(_rows(ax, win))
        names = {_nfc(staged.name), _nfc(staged.stem)}
        keep = True
        ax.perform(button, "AXPress")                  # 여기부터는 나갔을 수 있다 (결과 코드는 믿지 않는다)

        def sent():
            rows = _rows(ax, win)
            fresh = rows[before:] if len(rows) > before else []
            return next((r for r in reversed(fresh) if _outgoing_attachment(ax, r, names)), None)

        row = _wait(sent, VERIFY_TIMEOUT, 0.4)
        if row is None:
            still = " 미리보기 창이 아직 열려 있습니다." if _sheet(ax, win) is not None else ""
            raise Uncertain(f"파일을 보냈는지 확인하지 못했습니다 — 다시 보내지 말고 방을 확인하세요.{still}")
        done = bool(_wait(lambda: not _uploading(ax, row), UPLOAD_TIMEOUT, 0.5))
        keep = not done
        return "파일 전송 확인" + ("" if done else " (아직 올리는 중 — 임시 파일은 한 시간 뒤 다음 실행 때 지웁니다)")
    except KakaoError:
        _cancel_sheet(ax, win)
        raise
    finally:
        if not keep:
            _unstage(staged)


# --- 명령줄 ----------------------------------------------------------------------


def _print(args, data, text: str) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else text)


def _file_arg(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        raise KakaoError(f"파일이 없습니다: {path}")
    path = path.resolve()
    if not path.is_file():
        raise KakaoError(f"파일 하나만 보낼 수 있습니다 (폴더 등은 안 됨): {path}")
    if path.stat().st_size == 0:
        raise KakaoError(f"빈 파일입니다: {path}")
    return path


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def main() -> int:
    ap = argparse.ArgumentParser(description="카카오톡 읽기·보내기 (macOS, 접근성)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("guide", help="처음 쓰는 사람용 — 준비 상태 점검과 사용법")
    p.add_argument("--auto", action="store_true",
                   help="처음이면 전체 안내, 이미 안내했으면 문제가 있을 때만 출력 (스킬이 매번 먼저 돌린다)")
    p = sub.add_parser("set-confirm", help="보내기 전 미리보기·확인을 켜고 끈다")
    p.add_argument("state", choices=["on", "off"])
    sub.add_parser("windows", help="열린 카카오톡 창 제목")
    p = sub.add_parser("read", help="방 창의 최근 메시지")
    p.add_argument("room")
    p.add_argument("-n", "--count", type=int, default=20)
    p = sub.add_parser("notices", help="'댓글 알림'에서 그 방 알림")
    p.add_argument("room")
    p.add_argument("--top", type=int, default=20)
    p = sub.add_parser("thread", help="원글의 스레드를 열어 읽고 닫는다")
    p.add_argument("room")
    p.add_argument("parent", help="원글 본문 (정확히)")
    p.add_argument("--nth", type=int, default=0, help="같은 본문이 여럿이면 최근에서 몇 번째 (0=가장 최근)")
    p.add_argument("--sender", help="원글을 쓴 사람 이름으로 좁히기")
    p = sub.add_parser("photo", help="사진의 글자 읽기 (저장 → 인식 → 파일 삭제)")
    p.add_argument("room")
    p.add_argument("near", help="사진 옆 메시지 본문 (정확히)")
    p.add_argument("--offset", type=int, default=-1, help="그 메시지에서 몇 줄 떨어진 사진인지 (-1=바로 위)")
    p.add_argument("--save-dir", default="~/Downloads", help="카카오톡 '저장하기'가 파일을 두는 폴더")
    p = sub.add_parser("send", help="일반 메시지 보내기 (--yes 없으면 미리보기)")
    p.add_argument("room")
    p.add_argument("text")
    p.add_argument("--yes", action="store_true", help="실제로 보낸다 (사용자 확인을 받은 뒤에만)")
    p = sub.add_parser("reply", help="메시지에 댓글(스레드)로 보내기 (--yes 없으면 미리보기)")
    p.add_argument("room")
    p.add_argument("target", help="댓글을 달 메시지 본문 (정확히)")
    p.add_argument("text")
    p.add_argument("--nth", type=int, default=0)
    p.add_argument("--sender")
    p.add_argument("--no-mirror", action="store_true", help="'채팅방에 함께 보내기'를 끈다 (스레드에만)")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("send-file", help="사진·파일 하나 보내기 (--yes 없으면 미리보기)")
    p.add_argument("room")
    p.add_argument("file", help="보낼 파일 경로")
    p.add_argument("--yes", action="store_true", help="실제로 보낸다 (사용자 확인을 받은 뒤에만)")
    for s in sub.choices.values():
        s.add_argument("--json", action="store_true")
        s.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.cmd == "set-confirm":
        set_confirm(args.state == "on")
        _print(args, {"confirm": args.state == "on", "settings": str(SETTINGS)},
               "보내기 전에 미리보기·확인을 거칩니다" if args.state == "on"
               else "이제 허용 목록의 방에는 미리보기 없이 바로 보냅니다")
        return 0
    if args.cmd == "guide":
        checks = guide_checks()
        mark = {True: "✅", False: "❌", None: "⚠️ "}
        missing = [c for c in checks if c.ok is False]
        first = not ONBOARDED.exists()
        if args.auto and not first:
            if missing:                      # 이미 안내한 사람 — 막힌 것만 알린다
                _print(args, {"first_run": False, "ready": False, "checks": [asdict(c) for c in missing]},
                       "준비가 덜 됐습니다\n" + "\n".join(f"  ❌ {c.item}: {c.detail}"
                                                    + (f"\n       → {c.fix}" if c.fix else "") for c in missing))
                return 1
            return 0                         # 아무것도 출력하지 않는다 → 바로 요청 처리
        lines = ["처음 쓰시는군요 — 준비 상태부터 점검했습니다\n" if first else ""]
        lines += ["준비 상태"] + [f"  {mark[c.ok]} {c.item}: {c.detail}" + (f"\n       → {c.fix}" if c.fix else "")
                                 for c in checks]
        lines.append("\n" + ("필수 준비가 끝났습니다." if not missing
                             else f"먼저 할 일 {len(missing)}가지: " + " · ".join(c.item for c in missing)))
        if first:
            SKILL_HOME.mkdir(parents=True, exist_ok=True)
            ONBOARDED.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
        _print(args, {"first_run": first, "ready": not missing, "checks": [asdict(c) for c in checks],
                      "usage": USAGE}, "\n".join(lines).lstrip("\n") + "\n\n" + USAGE)
        return 0 if not missing else 1
    try:
        path = _file_arg(args.file) if args.cmd == "send-file" else None
        if args.cmd in ("send", "reply", "send-file"):
            allowed = args.room in allowed_rooms()
            if not args.yes and confirm_before_send():
                preview = {"preview": True, "action": args.cmd, "room": args.room, "allowed": allowed}
                lines = [f"미리보기 — 보내지 않았습니다. 방: {args.room}" + ("" if allowed else " ⚠️ 허용 목록에 없음")]
                if args.cmd == "send-file":
                    size = path.stat().st_size
                    preview.update(file=str(path), name=path.name, bytes=size)
                    lines.append(f"보낼 파일: {path.name} ({_human(size)}) — {path}")
                else:
                    preview["text"] = args.text
                    if args.cmd == "reply":
                        preview.update(target=args.target, mirror_to_room=not args.no_mirror)
                        lines.append(f"댓글 달 메시지: {args.target} "
                                     f"({'방에도 함께' if not args.no_mirror else '스레드에만'})")
                    lines.append(f"보낼 글: {args.text}")
                lines.append("실제로 보내려면 같은 명령에 --yes (확인을 끄려면 set-confirm off)")
                _print(args, preview, "\n".join(lines))
                return 0
            check_allowed(args.room)
        if (args.cmd in ("thread", "photo", "send", "reply", "send-file") and bot_running()
                and not args.force):
            raise KakaoError("kakaobot 봇이 돌고 있어 카카오톡 창 조작이 겹칠 수 있습니다 — "
                             "메뉴바에서 봇을 멈춘 뒤 다시 하세요")
        ax = AX()
        if args.cmd == "windows":
            wins = [{"title": ax.attr(w, "AXTitle") or "", "state": state} for w, state in ax.all_windows()]
            _print(args, wins, "\n".join(f"{w['title'] or '(제목 없음 — 스레드 창 등)'}"
                                         + ("" if w["state"] == "보임" else f"  ({w['state']})") for w in wins))
        elif args.cmd == "read":
            msgs, scrolled = read_room(ax, args.room, args.count)
            if scrolled:
                print("⚠️ 방 창이 위로 스크롤돼 있어 최근 메시지가 아닐 수 있습니다", file=sys.stderr)
            _print(args, {"room": args.room, "scrolled_up": scrolled, "messages": [asdict(m) for m in msgs]},
                   "\n".join(f"[{'공지' if m.system else ('나' if m.outgoing else (m.sender or '?'))}]"
                             f"{' [스레드 댓글]' if m.thread_comment else ''} {m.text}"
                             + (f"  ({m.time})" if m.time else "") for m in msgs))
        elif args.cmd == "notices":
            ns = read_notices(ax, args.room, args.top)
            _print(args, [asdict(n) for n in ns],
                   "\n".join(f"{n.time} {'●' if n.unread else ' '} 원글: {n.parent}  →  최신 댓글: {n.latest}"
                             for n in ns))
        elif args.cmd == "thread":
            ms = read_thread(ax, args.room, args.parent, args.sender, args.nth)
            _print(args, [asdict(m) for m in ms],
                   "\n".join(f"[{'원글' if i == 0 else ('나' if m.outgoing else (m.sender or '?'))}] {m.text}"
                             for i, m in enumerate(ms)))
        elif args.cmd == "photo":
            path = save_photo(ax, args.room, args.near, args.offset, Path(args.save_dir).expanduser())
            try:
                text = ocr(path)
            finally:
                path.unlink(missing_ok=True)           # 받은 사진은 읽자마자 지운다
            _print(args, {"text": text, "deleted": str(path)}, text or "(글자를 찾지 못했습니다)")
        elif args.cmd == "send":
            _print(args, {"sent": True, "detail": send_plain(ax, args.room, args.text)}, "✅ 보냈습니다")
        elif args.cmd == "reply":
            detail = reply_in_thread(ax, args.room, args.target, args.text, mirror=not args.no_mirror,
                                     sender=args.sender, nth=args.nth)
            _print(args, {"sent": True, "detail": detail}, f"✅ {detail}")
        elif args.cmd == "send-file":
            detail = send_file(ax, args.room, path)
            _print(args, {"sent": True, "detail": detail, "file": str(path)}, f"✅ {detail}: {path.name}")
    except Uncertain as exc:
        print(f"⚠️ {exc}", file=sys.stderr)
        return 3
    except KakaoError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
