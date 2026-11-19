"""윈도우 — Win32 메시지로 읽고 보낸다 (카카오톡 26.10 실측).

카카오톡 윈도우 방 창은 UI Automation 에 메시지 목록을 내주지 않는다 (probe 결과: 입력창
RICHEDIT50W 와 빈 목록 컨트롤 EVA_VH_ListControl_Dblclk 뿐). 그래서

  읽기   목록 컨트롤에 Ctrl+A·Ctrl+C 키 메시지를 보내 대화를 클립보드로 받는다.
         창을 앞으로 가져오지 않는다. 원래 클립보드 글은 읽은 뒤 되돌려 놓는다.
  보내기 입력창에 WM_CHAR 로 글을 치고(카카오톡이 입력을 알아차리게) Enter 키 메시지를 보낸 뒤,
         다시 읽어 새 말풍선이 생겼는지 확인한다. 안 나간 게 확실하면 최대 3번까지 다시 한다.
         창을 앞으로 가져오지 않는다.

복사한 글 모양:
    2026년 11월 19일 목요일
    [보낸 사람] [오후 3:45] 첫 줄
    이어지는 줄
내가 보낸 메시지도 [내 이름] 으로 나온다. 내 이름은 KAKAO_MY_NAME 환경 변수,
1:1 방(방 제목이 아닌 쪽), 또는 이 프로그램이 보낸 메시지에서 알아낸다.
"""
from __future__ import annotations

import os
import re
import sys
import time

from kakao_read.backends.base import BackendError, ChatMessage, SendUncertain, Snapshot

ROOM_CLASS = "EVA_Window_Dblclk"
LIST_CLASS = "EVA_VH_ListControl_Dblclk"
INPUT_CLASS = "RICHEDIT50W"
KAKAO_EXE = "kakaotalk.exe"

WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
VK_RETURN, VK_CONTROL, VK_A, VK_C, VK_END = 0x0D, 0x11, 0x41, 0x43, 0x23
WM_MOUSEWHEEL, WHEEL_DELTA = 0x020A, 120
HISTORY_NOTCHES = 15      # 한 번에 올리는 휠 칸 수 — 카카오톡이 과거 메시지를 약 30개씩 불러온다 (실측)
HISTORY_STALL = 3         # 이만큼 연달아 늘지 않으면 대화 처음(또는 불러오기 한도)으로 본다
CF_UNICODETEXT = 13
SMTO_ABORTIFHUNG = 0x0002

HEAD = re.compile(r"^\[(?P<sender>.+?)\] \[(?P<time>(?:오전|오후|AM|PM) \d{1,2}:\d{2})\] ?(?P<text>.*)$")
DATE_LINE = re.compile(r"^\d{4}년 \d{1,2}월 \d{1,2}일 [월화수목금토일]요일$")
SYSTEM_LINE = re.compile(r"님이 (들어왔습니다|나갔습니다|.*초대했습니다|.*내보냈습니다)\.?$|^채팅방 관리자가")


PLACEHOLDER = "메시지 입력"   # 빈 입력창도 WM_GETTEXT 로 이 글을 돌려준다


SEND_ATTEMPTS = 3   # Enter 를 보내고 확인하는 횟수 (안 나간 게 확실할 때만 다시)


def _empty(box_text: str | None) -> bool:
    t = (box_text or "").strip()
    return not t or t == PLACEHOLDER


def _norm(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


class _Win32:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ct, self.wt = ctypes, wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u, self.k = u, k
        self.EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        for fn, args, res in [
            (u.EnumWindows, [self.EnumProc, wt.LPARAM], wt.BOOL),
            (u.FindWindowExW, [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR], wt.HWND),
            (u.GetClassNameW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.GetWindowTextLengthW, [wt.HWND], ctypes.c_int),
            (u.GetWindowTextW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.IsWindowVisible, [wt.HWND], wt.BOOL),
            (u.GetWindowRect, [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            (u.IsIconic, [wt.HWND], wt.BOOL),
            (u.GetWindowThreadProcessId, [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            (u.PostMessageW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL),
            (u.SendMessageTimeoutW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPVOID, wt.UINT, wt.UINT,
                                     ctypes.POINTER(ctypes.c_size_t)], wt.LPARAM),
            (u.MapVirtualKeyW, [wt.UINT, wt.UINT], wt.UINT),
            (u.AttachThreadInput, [wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
            (u.GetKeyboardState, [ctypes.c_void_p], wt.BOOL),
            (u.SetKeyboardState, [ctypes.c_void_p], wt.BOOL),
            (u.OpenClipboard, [wt.HWND], wt.BOOL),
            (u.CloseClipboard, [], wt.BOOL),
            (u.EmptyClipboard, [], wt.BOOL),
            (u.GetClipboardData, [wt.UINT], wt.HANDLE),
            (u.SetClipboardData, [wt.UINT, wt.HANDLE], wt.HANDLE),
            (u.IsClipboardFormatAvailable, [wt.UINT], wt.BOOL),
            (u.CountClipboardFormats, [], ctypes.c_int),
            (u.GetClipboardSequenceNumber, [], wt.DWORD),
            (k.GetCurrentThreadId, [], wt.DWORD),
            (k.GlobalAlloc, [wt.UINT, ctypes.c_size_t], wt.HGLOBAL),
            (k.GlobalLock, [wt.HGLOBAL], ctypes.c_void_p),
            (k.GlobalUnlock, [wt.HGLOBAL], wt.BOOL),
            (k.GlobalFree, [wt.HGLOBAL], wt.HGLOBAL),
            (k.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (k.QueryFullProcessImageNameW, [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)],
             wt.BOOL),
            (k.CloseHandle, [wt.HANDLE], wt.BOOL),
        ]:
            fn.argtypes, fn.restype = args, res

    # ── 창 ──
    def top_windows(self) -> list[int]:
        found: list[int] = []

        @self.EnumProc
        def cb(hwnd, _):
            if hwnd:
                found.append(hwnd)
            return True

        self.u.EnumWindows(cb, 0)
        return found

    def class_name(self, hwnd: int) -> str:
        buf = self.ct.create_unicode_buffer(256)
        return buf.value if self.u.GetClassNameW(hwnd, buf, 256) else ""

    def title(self, hwnd: int) -> str:
        n = self.u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = self.ct.create_unicode_buffer(n + 1)
        self.u.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def child(self, hwnd: int, cls: str) -> int:
        return self.u.FindWindowExW(hwnd, None, cls, None) or 0

    def thread_and_pid(self, hwnd: int) -> tuple[int, int]:
        pid = self.wt.DWORD()
        tid = self.u.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        return tid, pid.value

    def exe_name(self, pid: int) -> str:
        h = self.k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            size = self.wt.DWORD(1024)
            buf = self.ct.create_unicode_buffer(size.value)
            ok = self.k.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(size))
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower() if ok else ""
        finally:
            self.k.CloseHandle(h)

    # ── 글 ──
    def get_text(self, hwnd: int) -> str | None:
        res = self.ct.c_size_t()
        if not self.u.SendMessageTimeoutW(hwnd, WM_GETTEXTLENGTH, 0, None, SMTO_ABORTIFHUNG, 1000,
                                          self.ct.byref(res)):
            return None
        size = res.value
        buf = self.ct.create_unicode_buffer(size + 1)
        if not self.u.SendMessageTimeoutW(hwnd, WM_GETTEXT, size + 1, self.ct.addressof(buf),
                                          SMTO_ABORTIFHUNG, 1000, self.ct.byref(res)):
            return None
        return buf.value

    def set_text(self, hwnd: int, text: str) -> bool:
        buf = self.ct.create_unicode_buffer(text)
        res = self.ct.c_size_t()
        ok = self.u.SendMessageTimeoutW(hwnd, WM_SETTEXT, 0, self.ct.addressof(buf), SMTO_ABORTIFHUNG,
                                        2000, self.ct.byref(res))
        return bool(ok and res.value)

    # ── 키 ──
    def key(self, hwnd: int, vk: int) -> None:
        sc = self.u.MapVirtualKeyW(vk, 0)
        self.u.PostMessageW(hwnd, WM_KEYDOWN, vk, 1 | (sc << 16))
        time.sleep(0.03)
        self.u.PostMessageW(hwnd, WM_KEYUP, vk, 1 | (sc << 16) | (3 << 30))

    def ctrl_keys(self, hwnd: int, vks: list[int]) -> None:
        """그 창 스레드의 키보드 상태에 Ctrl 을 눌린 것으로 해 두고 키 메시지를 보낸다 (전면화 없이)."""
        tid, _ = self.thread_and_pid(hwnd)
        me = self.k.GetCurrentThreadId()
        attached = bool(self.u.AttachThreadInput(me, tid, True))
        state = (self.ct.c_ubyte * 256)()
        self.u.GetKeyboardState(state)
        saved = bytes(state)
        try:
            state[VK_CONTROL] |= 0x80
            self.u.SetKeyboardState(state)
            for vk in vks:
                self.key(hwnd, vk)
                time.sleep(0.12)
            time.sleep(0.2)   # 카카오톡이 메시지를 처리할 동안 Ctrl 상태를 유지
        finally:
            restore = (self.ct.c_ubyte * 256).from_buffer_copy(saved)
            self.u.SetKeyboardState(restore)
            if attached:
                self.u.AttachThreadInput(me, tid, False)

    def wheel(self, hwnd: int, notches: int) -> None:
        """마우스 휠 메시지 (양수 = 위로). 실제 마우스·포커스는 건드리지 않는다."""
        r = self.wt.RECT()
        self.u.GetWindowRect(hwnd, self.ct.byref(r))
        lp = ((((r.top + r.bottom) // 2) & 0xFFFF) << 16) | (((r.left + r.right) // 2) & 0xFFFF)
        delta = WHEEL_DELTA if notches > 0 else -WHEEL_DELTA
        for _ in range(abs(notches)):
            self.u.PostMessageW(hwnd, WM_MOUSEWHEEL, (delta & 0xFFFF) << 16, lp)
            time.sleep(0.03)

    def _send(self, hwnd: int, msg: int, wp: int, lp: int) -> None:
        res = self.ct.c_size_t()
        self.u.SendMessageTimeoutW(hwnd, msg, wp, lp, SMTO_ABORTIFHUNG, 2000, self.ct.byref(res))

    def type_chars(self, hwnd: int, text: str) -> None:
        """WM_CHAR 로 한 자씩 — 카카오톡이 '입력된 글'로 알아차리는 길 (WM_SETTEXT 는 모른다)."""
        data = text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-16-le")
        for i in range(0, len(data), 2):            # UTF-16 단위 (이모지는 서로게이트 두 개)
            self._send(hwnd, WM_CHAR, int.from_bytes(data[i:i + 2], "little"), 1)

    def key_direct(self, hwnd: int, vk: int) -> None:
        """입력창 창 프로시저에 바로 (SendMessage). Enter 면 전송이 아니라 줄바꿈이 들어간다."""
        sc = self.u.MapVirtualKeyW(vk, 0)
        self._send(hwnd, WM_KEYDOWN, vk, 1 | (sc << 16))
        self._send(hwnd, WM_KEYUP, vk, 1 | (sc << 16) | (3 << 30))

    # ── 클립보드 ──
    def _open_clipboard(self) -> bool:
        for _ in range(40):
            if self.u.OpenClipboard(None):
                return True
            time.sleep(0.025)
        return False

    def clipboard_text(self) -> str | None:
        if not self._open_clipboard():
            return None
        try:
            h = self.u.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            p = self.k.GlobalLock(h)
            if not p:
                return None
            try:
                return self.ct.wstring_at(p)
            finally:
                self.k.GlobalUnlock(h)
        finally:
            self.u.CloseClipboard()

    def clipboard_has_only_text(self) -> bool:
        """비었거나 글만 있는 클립보드 — 되돌려 놓을 수 있다. 그림·파일이 있으면 False."""
        if not self._open_clipboard():
            return False
        try:
            n = self.u.CountClipboardFormats()
            return n == 0 or bool(self.u.IsClipboardFormatAvailable(CF_UNICODETEXT))
        finally:
            self.u.CloseClipboard()

    def set_clipboard_text(self, text: str | None) -> None:
        if not self._open_clipboard():
            return
        try:
            self.u.EmptyClipboard()
            if text is None:
                return
            data = self.ct.create_unicode_buffer(text)
            size = self.ct.sizeof(data)
            h = self.k.GlobalAlloc(0x0002, size)   # GMEM_MOVEABLE
            if not h:
                return
            p = self.k.GlobalLock(h)
            self.ct.memmove(p, data, size)
            self.k.GlobalUnlock(h)
            if not self.u.SetClipboardData(CF_UNICODETEXT, h):
                self.k.GlobalFree(h)
        finally:
            self.u.CloseClipboard()


def parse(text: str) -> list[ChatMessage]:
    """복사한 대화 글 → 메시지 (오래된 것부터). 날짜 줄은 버린다."""
    out: list[ChatMessage] = []
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur is not None:
            out.append(ChatMessage("\n".join(cur["lines"]).strip(), cur["sender"], False, cur["system"],
                                   time=cur["time"]))
        cur = None

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        m = HEAD.match(line)
        if m:
            flush()
            cur = {"sender": m["sender"], "time": m["time"], "lines": [m["text"]], "system": False}
        elif DATE_LINE.match(line.strip()):
            flush()
        elif SYSTEM_LINE.search(line.strip()):
            flush()
            cur = {"sender": None, "time": None, "lines": [line.strip()], "system": True}
            flush()
        elif cur is not None and not cur["system"]:
            cur["lines"].append(line)
        elif line.strip():
            flush()
            cur = {"sender": None, "time": None, "lines": [line.strip()], "system": True}
            flush()
    flush()
    return [m for m in out if m.text]


class WindowsBackend:
    name = "Windows 창 메시지(클립보드 읽기)"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise BackendError("Windows 전용 백엔드입니다")
        self.w = _Win32()
        self.my_name: str | None = os.environ.get("KAKAO_MY_NAME") or None

    # ── 창 찾기 ──
    def _rooms(self) -> list[int]:
        w = self.w
        out, exe = [], {}
        for hwnd in w.top_windows():
            if w.class_name(hwnd) != ROOM_CLASS:
                continue
            _, pid = w.thread_and_pid(hwnd)
            if pid not in exe:
                exe[pid] = w.exe_name(pid)
            if exe[pid] != KAKAO_EXE:
                continue
            if not (w.child(hwnd, LIST_CLASS) and w.child(hwnd, INPUT_CLASS)):
                continue   # 친구 목록 같은 메인 창
            out.append(hwnd)
        return out

    def _room(self, room: str) -> int:
        wins = self._rooms()
        hits = [h for h in wins if self.w.title(h).strip() == room.strip()]
        if not hits:
            titles = ", ".join(f'"{self.w.title(h)}"' for h in wins) or "(없음)"
            raise BackendError(f'"{room}" 방 창을 찾지 못했습니다. 열린 방 창: {titles} — '
                               "카카오톡에서 방을 더블클릭해 별도 창으로 여세요")
        if len(hits) > 1:
            raise BackendError(f'"{room}" 방 창이 {len(hits)}개입니다 — 하나만 남기고 닫으세요')
        return hits[0]

    def windows(self) -> list[tuple[str, str]]:
        w = self.w
        out = []
        for h in self._rooms():
            state = "최소화" if w.u.IsIconic(h) else ("보임" if w.u.IsWindowVisible(h) else "숨김")
            out.append((w.title(h), state))
        return out

    # ── 읽기 ──
    def _copy_all(self, hwnd: int) -> str:
        w = self.w
        lst = w.child(hwnd, LIST_CLASS)
        if not lst:
            raise BackendError("방 창에서 메시지 목록을 찾지 못했습니다")
        restorable = w.clipboard_has_only_text()
        saved = w.clipboard_text() if restorable else None
        before = w.u.GetClipboardSequenceNumber()
        try:
            # 가끔 복사가 안 될 때가 있다 (실측) — 키 메시지를 다시 보내 최대 3번
            for _ in range(3):
                w.ctrl_keys(lst, [VK_A, VK_C])
                deadline = time.monotonic() + 1.5
                while w.u.GetClipboardSequenceNumber() == before and time.monotonic() < deadline:
                    time.sleep(0.05)
                if w.u.GetClipboardSequenceNumber() != before:
                    text = w.clipboard_text()
                    if text is not None:
                        return text
            raise BackendError("대화를 복사하지 못했습니다 — 방 창이 열려 있는지(최소화 X) 확인하세요")
        finally:
            if restorable and w.u.GetClipboardSequenceNumber() != before:
                w.set_clipboard_text(saved)

    def _mark(self, room: str, msgs: list[ChatMessage]) -> list[ChatMessage]:
        me = self.my_name
        if me is None:
            senders = {m.sender for m in msgs if m.sender}
            others = senders - {room}
            if room in senders and len(others) == 1:   # 1:1 방: 방 제목이 아닌 쪽이 나
                me = others.pop()
        return [ChatMessage(m.text, m.sender, bool(me) and m.sender == me, m.system, False, m.time)
                for m in msgs]

    def _read_all(self, hwnd: int, room: str) -> list[ChatMessage]:
        return self._mark(room, parse(self._copy_all(hwnd)))

    def read(self, room: str, count: int) -> Snapshot:
        msgs = self._read_all(self._room(room), room)
        return Snapshot(msgs[-count:] if count > 0 else msgs, False)

    def history(self, room: str, max_messages: int, progress=None) -> list[ChatMessage]:
        """방 목록을 휠 메시지로 위로 올려 지난 대화를 불러온 뒤 모두 읽는다 (오래된 것부터).
        끝나면 맨 아래로 되돌린다. 포커스는 건드리지 않는다. 메시지 수백 개면 수십 초 걸린다."""
        w = self.w
        hwnd = self._room(room)
        lst = w.child(hwnd, LIST_CLASS)
        if not lst:
            raise BackendError("방 창에서 메시지 목록을 찾지 못했습니다")
        msgs = parse(self._copy_all(hwnd))
        rounds = stall = 0
        try:
            while len(msgs) < max_messages and stall < HISTORY_STALL:
                w.wheel(lst, HISTORY_NOTCHES)
                rounds += 1
                time.sleep(1.0)
                more = parse(self._copy_all(hwnd))
                stall = stall + 1 if len(more) <= len(msgs) else 0
                msgs = more if len(more) >= len(msgs) else msgs
                if progress:
                    progress(len(msgs))
        finally:
            w.wheel(lst, -(rounds * HISTORY_NOTCHES + 30))
            time.sleep(0.5)
            w.key(lst, VK_END)
        return self._mark(room, msgs)[-max_messages:]

    # ── 보내기 ──
    def send(self, room: str, text: str) -> str:
        text = text.strip()
        if not text:
            raise BackendError("빈 메시지는 보내지 않습니다")
        w = self.w
        hwnd = self._room(room)
        if w.u.IsIconic(hwnd):
            raise BackendError("방 창이 최소화돼 있습니다 — 최소화를 풀어 주세요")
        box = w.child(hwnd, INPUT_CLASS)
        if not box:
            raise BackendError("방 창에서 입력창을 찾지 못했습니다")
        draft = w.get_text(box)
        if draft is None:
            raise BackendError("입력창을 읽지 못했습니다 (카카오톡이 응답하지 않음)")
        if not _empty(draft):
            raise BackendError("입력창에 쓰다 만 글이 있어 보내지 않았습니다 — 비우고 다시 하세요")
        before = len(parse(self._copy_all(hwnd)))
        want = _norm(text)

        # 카카오톡은 WM_SETTEXT 로 넣은 글을 '입력된 글'로 치지 않아 Enter 를 눌러도 보낼 게 없다고 본다
        # (전송 버튼도 꺼진 채). 그래서 매 시도마다
        #   1) WM_CHAR 로 한 자씩 친다          → 카카오톡이 입력을 알아차린다
        #   2) 입력창에 Enter 를 바로 보낸다     → 전송이 아니라 줄바꿈 (입력 상태를 확정시키는 단계)
        #   3) WM_SETTEXT 로 정확한 글로 맞춘다  → 2)의 줄바꿈을 지운다
        #   4) Enter 를 PostMessage 로 보낸다    → 카카오톡 메시지 루프가 '전송'으로 받는다
        # 창을 앞으로 가져오지 않는다 (26.10 실측: 포커스를 줘도 1)~3) 없이는 안 나가고, 있으면 없이도 나간다).
        # 한 번 시도할 때마다 결과를 확인하고, 입력창에 글이 그대로 남아 '안 나간 게 확실할 때만' 다시 한다.
        # 글이 사라졌는데 말풍선이 안 보이면 나갔을 수 있으니 다시 보내지 않는다 (중복 방지).
        for attempt in range(1, SEND_ATTEMPTS + 1):
            w.set_text(box, "")
            w.type_chars(box, want)
            time.sleep(0.2)
            w.key_direct(box, VK_RETURN)
            time.sleep(0.2)
            if not w.set_text(box, want.replace("\n", "\r\n")):
                w.set_text(box, "")
                raise BackendError("입력창에 글을 넣지 못했습니다")
            time.sleep(0.15)
            if (w.get_text(box) or "").replace("\r\n", "\n") != want:
                w.set_text(box, "")
                raise BackendError("입력창에 넣은 글이 달라 보내지 않았습니다")
            w.key(box, VK_RETURN)
            state, msg = self._confirm(hwnd, box, before, want)
            if state == "sent":
                self.my_name = self.my_name or msg.sender
                tries = "" if attempt == 1 else f" · {attempt}번째 시도"
                return f"전송 확인 ({msg.sender} · {msg.time}{tries})"
            if state == "unknown":
                raise SendUncertain(f"{attempt}번째 시도: 입력창은 비었지만 새 말풍선을 확인하지 못했습니다 — "
                                    "다시 보내지 말고 방을 확인하세요")
            time.sleep(0.3 * attempt)   # 'stuck' — 아직 안 나감. 잠깐 쉬고 다시
        w.set_text(box, "")
        raise BackendError(f"Enter 를 {SEND_ATTEMPTS}번 보냈지만 먹지 않아 보내지 않았습니다 (입력창을 비웠습니다)")

    def _confirm(self, hwnd: int, box: int, before: int, want: str) -> tuple[str, ChatMessage | None]:
        """Enter 뒤 결과: sent(새 말풍선 확인) · stuck(입력창에 글이 그대로) · unknown(비었는데 말풍선 없음)."""
        w = self.w
        stuck_until = time.monotonic() + 1.5    # 이 안에 입력창이 안 비면 안 나간 것
        cleared_at = None
        while True:
            time.sleep(0.3)
            now = time.monotonic()
            if cleared_at is None:
                if _empty(w.get_text(box)):
                    cleared_at = now
                elif now >= stuck_until:
                    return "stuck", None
                else:
                    continue
            try:
                new = parse(self._copy_all(hwnd))[before:]
            except BackendError:   # 확인용 복사 실패 — 나갔을 수 있으니 오류로 끝내지 말고 계속 본다
                new = []
            mine = [m for m in new if m.sender and _norm(m.text) == want]
            if mine:
                return "sent", mine[-1]
            if now - cleared_at > 4.0:
                return "unknown", None
