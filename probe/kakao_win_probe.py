# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = ["comtypes>=1.4.13; sys_platform == 'win32'"]
# ///
"""윈도우 카카오톡 PC 에서 대화 내용을 '건드리지 않고' 읽을 수 있는지 확인한다 (읽기 전용).

macOS kakao-read 스킬은 접근성(AX)으로 방 창의 메시지를 읽는다. 윈도우에서 같은 일을
UI Automation(UIA)으로 할 수 있는지 보려고, 방 창의 Win32 자식 창과 UIA 트리를 덤프하고
화면에 보이는 메시지 문구(--expect)가 어느 노드의 어느 속성에서 나오는지 찾는다.

하지 않는 것: 창 전면화·포커스 이동·클릭·키 입력·클립보드 사용·시스템 설정 변경.
읽는 것: 창 제목·클래스, WM_GETTEXT, UIA 속성(Name·Value·LegacyIAccessible·TextPattern).

    uv run kakao_win_probe.py windows
    uv run kakao_win_probe.py probe "방 이름" --expect "화면에 보이는 최근 메시지 일부"
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass

KAKAO_PROCESS = "kakaotalk"   # 실행 파일 이름(KakaoTalk.exe)에 이 글이 들어 있으면 카카오톡 창
DEFAULT_REPORT = "probe-report.json"
PREVIEW_MAX = 12    # 이보다 긴 글은 --full 이 아니면 앞 PREVIEW_KEEP 자만 남긴다
PREVIEW_KEEP = 8
TEXT_LIMIT = 4000   # TextPattern 에서 한 번에 읽을 최대 글자 수
WIN32_PRINT_MAX = 60
ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))
# 날짜·시각만으로 된 글("2026년 10월 9일 금요일", "1\n오후 6:29")은 자르지 않는다 — 형식을 봐야 한다
DATE_TOKENS = re.compile(r"\d{4}\s*[년./-]|\d{1,2}\s*[월일./-]|[월화수목금토일]요일|오전|오후|AM|PM"
                         r"|\d{1,2}:\d{2}(:\d{2})?|\d+", re.IGNORECASE)
DATE_WORDS = re.compile(r"[년월일]|요일|오전|오후|\d:\d{2}")

CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox", 50004: "Edit",
    50005: "Hyperlink", 50006: "Image", 50007: "ListItem", 50008: "List", 50009: "Menu",
    50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar", 50013: "RadioButton",
    50014: "ScrollBar", 50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid",
    50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window", 50033: "Pane",
    50034: "Header", 50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
}
# 있는지 기록할 패턴. 값은 Value·Text·LegacyIAccessible 에서만 읽는다 (Invoke 같은 동작은 부르지 않는다)
PATTERNS = {
    "Value": 10002, "Scroll": 10004, "SelectionItem": 10010, "Text": 10014,
    "LegacyIAccessible": 10018, "ItemContainer": 10019, "VirtualizedItem": 10020,
}
MSAA_ROLES = {
    9: "window", 10: "client", 15: "document", 16: "pane", 20: "grouping", 28: "row", 29: "cell",
    30: "link", 33: "list", 34: "listitem", 40: "graphic", 41: "statictext", 42: "text",
    43: "pushbutton",
}
TEXT_FIELDS = ("name", "value", "text", "legacy_name", "legacy_value", "legacy_description",
               "legacy_help", "help")


class ProbeError(Exception):
    pass


# ── 글 다루기 ────────────────────────────────────────────────────────────────

def clean(value) -> str | None:
    """None·빈 글은 None. 폭 없는 공백은 지운다 (카카오톡이 글 끝에 붙이곤 한다)."""
    if value is None:
        return None
    text = str(value).translate(ZERO_WIDTH)
    text = text.encode("utf-8", "replace").decode("utf-8")  # 글자 수 제한에 잘린 이모지 반쪽은 ? 로
    return text if text.strip() else None


def squash(text: str) -> str:
    """찾기용: 공백을 모두 지우고 소문자로."""
    return "".join(text.split()).lower()


def one_line(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ⏎ ")


def looks_like_date(text: str) -> bool:
    """날짜·시각 낱말과 숫자만 있는 글. 숫자만 긴 글(계좌·전화번호)은 아니다."""
    rest = DATE_TOKENS.sub("", text).strip(" .,-/()\n\r\t")
    return len(text) <= 40 and not rest and bool(DATE_WORDS.search(text))


class Masker:
    """긴 글은 앞부분만 남긴다 (--full 이면 그대로). 보고서에 대화 원문이 통째로 남지 않게."""

    def __init__(self, full: bool) -> None:
        self.full = full

    def __call__(self, text: str | None) -> str | None:
        if text is None or self.full or len(text) <= PREVIEW_MAX or looks_like_date(text):
            return text
        return f"{text[:PREVIEW_KEEP]}…(+{len(text) - PREVIEW_KEEP}자)"


@dataclass
class Hit:
    section: str   # uia · uia-extra · win32
    path: str      # 루트에서 그 노드까지
    field: str     # name · legacy_value · wm_gettext …
    text: str      # 원문 — 보고서에는 마스킹해서 쓴다


class TextIndex:
    """읽은 글을 모아 두고 --expect 문구를 찾는다."""

    def __init__(self) -> None:
        self.hits: list[Hit] = []

    def add(self, section: str, path: str, texts: dict[str, str]) -> None:
        self.hits.extend(Hit(section, path, field, text) for field, text in texts.items())

    def find(self, needle: str) -> list[Hit]:
        key = squash(needle)
        return [h for h in self.hits if key and key in squash(h.text)]


# ── Win32 (ctypes, 읽기만) ───────────────────────────────────────────────────

class Win32:
    WM_GETTEXT = 0x000D
    WM_GETTEXTLENGTH = 0x000E
    SMTO_ABORTIFHUNG = 0x0002
    SPI_GETSCREENREADER = 0x0046
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt

        self.ct, self.wt = ctypes, wt
        u = ctypes.WinDLL("user32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.u, self.k = u, k
        try:  # 이 프로세스만 DPI 인식(-4: PER_MONITOR_AWARE_V2) — Win32 창 좌표를 UIA 처럼 물리 픽셀로
            u.SetProcessDpiAwarenessContext.argtypes = [wt.HANDLE]
            u.SetProcessDpiAwarenessContext.restype = wt.BOOL
            u.SetProcessDpiAwarenessContext(wt.HANDLE(-4))
        except (AttributeError, OSError):
            pass
        self.EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        for fn, args, res in [
            (u.EnumWindows, [self.EnumProc, wt.LPARAM], wt.BOOL),
            (u.EnumChildWindows, [wt.HWND, self.EnumProc, wt.LPARAM], wt.BOOL),
            (u.GetClassNameW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.GetWindowTextLengthW, [wt.HWND], ctypes.c_int),
            (u.GetWindowTextW, [wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            (u.IsWindowVisible, [wt.HWND], wt.BOOL),
            (u.IsIconic, [wt.HWND], wt.BOOL),
            (u.GetParent, [wt.HWND], wt.HWND),
            (u.GetWindowRect, [wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
            (u.GetWindowThreadProcessId, [wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            (u.SendMessageTimeoutW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                     ctypes.POINTER(ctypes.c_size_t)], wt.LPARAM),
            (u.SystemParametersInfoW, [wt.UINT, wt.UINT, wt.LPVOID, wt.UINT], wt.BOOL),
            (k.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (k.QueryFullProcessImageNameW, [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                            ctypes.POINTER(wt.DWORD)], wt.BOOL),
            (k.CloseHandle, [wt.HANDLE], wt.BOOL),
        ]:
            fn.argtypes, fn.restype = args, res

    def _enum(self, fn, *lead) -> list[int]:
        found: list[int] = []

        @self.EnumProc
        def callback(hwnd, _lparam):
            if hwnd:
                found.append(hwnd)
            return True

        fn(*lead, callback, 0)
        return found

    def top_windows(self) -> list[int]:
        return self._enum(self.u.EnumWindows)

    def descendants(self, hwnd: int) -> list[int]:
        """자식·손자 창 전부 (부모가 먼저 나온다)."""
        return self._enum(self.u.EnumChildWindows, hwnd)

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

    def message_text(self, hwnd: int, limit: int = 8192) -> str | None:
        """WM_GETTEXT — 다른 프로세스 창도 시스템이 글을 넘겨 준다. 응답이 없으면 None."""
        result = self.ct.c_size_t()
        if not self.u.SendMessageTimeoutW(hwnd, self.WM_GETTEXTLENGTH, 0, 0, self.SMTO_ABORTIFHUNG,
                                          300, self.ct.byref(result)):
            return None
        size = min(result.value, limit)
        if size == 0:
            return ""
        buf = self.ct.create_unicode_buffer(size + 1)
        if not self.u.SendMessageTimeoutW(hwnd, self.WM_GETTEXT, size + 1, self.ct.addressof(buf),
                                          self.SMTO_ABORTIFHUNG, 300, self.ct.byref(result)):
            return None
        return buf.value

    def visible(self, hwnd: int) -> bool:
        return bool(self.u.IsWindowVisible(hwnd))

    def minimized(self, hwnd: int) -> bool:
        return bool(self.u.IsIconic(hwnd))

    def parent(self, hwnd: int) -> int:
        return self.u.GetParent(hwnd) or 0

    def rect(self, hwnd: int) -> list[int] | None:
        r = self.wt.RECT()
        if not self.u.GetWindowRect(hwnd, self.ct.byref(r)):
            return None
        return [r.left, r.top, r.right - r.left, r.bottom - r.top]

    def pid(self, hwnd: int) -> int:
        pid = self.wt.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        return pid.value

    def exe_path(self, pid: int) -> str | None:
        handle = self.k.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = self.wt.DWORD(1024)
            buf = self.ct.create_unicode_buffer(size.value)
            ok = self.k.QueryFullProcessImageNameW(handle, 0, buf, self.ct.byref(size))
            return buf.value if ok else None
        finally:
            self.k.CloseHandle(handle)

    def screen_reader(self) -> bool | None:
        """스크린리더 플래그 (읽기만). 앱이 이 값을 보고 접근성 정보를 켜는 경우가 있다."""
        flag = self.wt.BOOL()
        ok = self.u.SystemParametersInfoW(self.SPI_GETSCREENREADER, 0, self.ct.byref(flag), 0)
        return bool(flag.value) if ok else None

    def file_version(self, path: str) -> str | None:
        ct, wt = self.ct, self.wt
        try:
            ver = ct.WinDLL("version")
            ver.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ct.POINTER(wt.DWORD)]
            ver.GetFileVersionInfoSizeW.restype = wt.DWORD
            ver.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID]
            ver.GetFileVersionInfoW.restype = wt.BOOL
            ver.VerQueryValueW.argtypes = [wt.LPCVOID, wt.LPCWSTR, ct.POINTER(wt.LPVOID),
                                           ct.POINTER(wt.UINT)]
            ver.VerQueryValueW.restype = wt.BOOL
            size = ver.GetFileVersionInfoSizeW(path, None)
            if not size:
                return None
            buf = ct.create_string_buffer(size)
            if not ver.GetFileVersionInfoW(path, 0, size, buf):
                return None
            ptr, length = wt.LPVOID(), wt.UINT()
            if not ver.VerQueryValueW(buf, "\\", ct.byref(ptr), ct.byref(length)) or not ptr.value:
                return None
            # VS_FIXEDFILEINFO: signature, strucVersion, fileVersionMS, fileVersionLS, …
            fixed = ct.cast(ptr, ct.POINTER(wt.DWORD * 4)).contents
            ms, ls = fixed[2], fixed[3]
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:
            return None


def exe_name(path: str | None) -> str:
    return (path or "").replace("/", "\\").rsplit("\\", 1)[-1]


def kakao_windows(w, include_hidden: bool = False) -> list[int]:
    """카카오톡 프로세스의 최상위 창 (기본: 보이는 창만 — 최소화된 창은 보이는 창에 든다)."""
    exe_by_pid: dict[int, str | None] = {}
    out = []
    for hwnd in w.top_windows():
        if not include_hidden and not w.visible(hwnd):
            continue
        pid = w.pid(hwnd)
        if pid not in exe_by_pid:
            exe_by_pid[pid] = w.exe_path(pid)
        if KAKAO_PROCESS not in exe_name(exe_by_pid[pid]).lower():
            continue
        if include_hidden and not w.visible(hwnd) and not w.title(hwnd):
            continue                            # 제목 없는 숨은 창은 수십 개라 뺀다
        out.append(hwnd)
    return out


def window_info(w, hwnd: int) -> dict:
    return {"hwnd": hex(hwnd), "title": w.title(hwnd), "class": w.class_name(hwnd),
            "visible": w.visible(hwnd), "minimized": w.minimized(hwnd), "rect": w.rect(hwnd)}


def resolve_target(w, room: str | None, hwnd_arg: str | None) -> int:
    if hwnd_arg:
        try:
            hwnd = int(hwnd_arg, 0)
        except ValueError:
            raise ProbeError(f"--hwnd 값을 읽지 못했습니다: {hwnd_arg}") from None
        if hwnd not in kakao_windows(w, include_hidden=True):
            raise ProbeError(f"{hwnd_arg} 는 카카오톡 창이 아닙니다 — windows 명령으로 확인하세요")
        return hwnd
    if not room:
        raise ProbeError("방 이름이나 --hwnd 를 주세요")
    wins = kakao_windows(w)
    matches = [h for h in wins if w.title(h).strip() == room.strip()]
    if not matches:
        titles = ", ".join(f'"{w.title(h)}"' for h in wins if w.title(h)) or "(없음)"
        raise ProbeError(f'"{room}" 창을 찾지 못했습니다. 지금 보이는 카카오톡 창: {titles}\n'
                         "방을 목록에서 더블클릭해 별도 창으로 열고, 최소화하지 마세요.")
    if len(matches) > 1:
        raise ProbeError(f'"{room}" 창이 {len(matches)}개입니다 — --hwnd 로 하나를 고르세요: '
                         + ", ".join(hex(h) for h in matches))
    return matches[0]


def win32_children(w, top: int, masker: Masker, index: TextIndex) -> list[dict]:
    """방 창 아래 Win32 창 전부: 클래스·제목·WM_GETTEXT."""
    rows = []
    depth_of = {top: 0}
    path_of = {top: w.class_name(top)}
    for hwnd in w.descendants(top):
        parent = w.parent(hwnd)
        cls = w.class_name(hwnd)
        depth_of[hwnd] = depth_of.get(parent, 0) + 1
        path_of[hwnd] = f"{path_of.get(parent, '?')} > {cls}"
        title = clean(w.title(hwnd))
        text = clean(w.message_text(hwnd))
        texts = {"title": title} if title else {}
        if text and text != title:
            texts["wm_gettext"] = text
        if texts:
            index.add("win32", path_of[hwnd], texts)
        rows.append({"hwnd": hex(hwnd), "parent": hex(parent) if parent else None,
                     "depth": depth_of[hwnd], "class": cls, "visible": w.visible(hwnd),
                     "rect": w.rect(hwnd), **{k: masker(v) for k, v in texts.items()}})
    return rows


# ── UI Automation (comtypes, 읽기만) ─────────────────────────────────────────

class UIA:
    """UIAutomationCore 를 comtypes 로 연다. RawView 로 걸어서 숨은 노드까지 본다."""

    def __init__(self) -> None:
        from comtypes.client import CreateObject, GetModule

        self.mod = GetModule("UIAutomationCore.dll")
        self.client = CreateObject(self.mod.CUIAutomation, interface=self.mod.IUIAutomation)
        self.walker = self.client.RawViewWalker

    def element(self, hwnd: int):
        return self.client.ElementFromHandle(hwnd)


@dataclass
class Limits:
    depth: int
    nodes: int
    seconds: float


def _get(obj, attr: str):
    try:
        return getattr(obj, attr)
    except Exception:
        return None


def _hex(handle) -> str | None:
    try:
        handle = getattr(handle, "value", handle)
        return hex(int(handle)) if handle else None
    except Exception:
        return None


def _rect(rect) -> list[int] | None:
    try:
        return [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]
    except Exception:
        return None


def read_node(uia, el) -> tuple[dict, dict[str, str]]:
    """UIA 노드 하나 → (구조 정보, 글 필드). 글은 원문 그대로 — 마스킹은 부른 쪽에서."""
    ct = _get(el, "CurrentControlType")
    info: dict = {
        "type": CONTROL_TYPES.get(ct, ct),
        "class": _get(el, "CurrentClassName") or None,
        "automation_id": _get(el, "CurrentAutomationId") or None,
        "framework": _get(el, "CurrentFrameworkId") or None,
        "hwnd": _hex(_get(el, "CurrentNativeWindowHandle")),
        "rect": _rect(_get(el, "CurrentBoundingRectangle")),
        "offscreen": _get(el, "CurrentIsOffscreen"),
    }
    texts = {"name": clean(_get(el, "CurrentName")), "help": clean(_get(el, "CurrentHelpText"))}
    patterns = []
    for pname, pid in PATTERNS.items():
        try:
            unknown = el.GetCurrentPattern(pid)
        except Exception:
            unknown = None
        if not unknown:
            continue
        patterns.append(pname)
        try:
            if pname == "Value":
                pat = unknown.QueryInterface(uia.mod.IUIAutomationValuePattern)
                texts["value"] = clean(_get(pat, "CurrentValue"))
            elif pname == "Text":
                pat = unknown.QueryInterface(uia.mod.IUIAutomationTextPattern)
                texts["text"] = clean(pat.DocumentRange.GetText(TEXT_LIMIT))
            elif pname == "LegacyIAccessible":
                pat = unknown.QueryInterface(uia.mod.IUIAutomationLegacyIAccessiblePattern)
                texts["legacy_name"] = clean(_get(pat, "CurrentName"))
                texts["legacy_value"] = clean(_get(pat, "CurrentValue"))
                texts["legacy_description"] = clean(_get(pat, "CurrentDescription"))
                texts["legacy_help"] = clean(_get(pat, "CurrentHelp"))
                info["legacy_role"] = _get(pat, "CurrentRole")
                info["legacy_child_id"] = _get(pat, "CurrentChildId")
        except Exception as exc:
            info.setdefault("errors", []).append(f"{pname}: {exc!r}"[:200])
    info["patterns"] = patterns
    return info, {k: v for k, v in texts.items() if v is not None}


def children(walker, el, deadline: float, cap: int = 5000) -> tuple[list, str | None]:
    """자식 노드들과, 끝까지 못 읽었으면 그 까닭."""
    kids: list = []
    try:
        kid = walker.GetFirstChildElement(el)
        while kid and len(kids) < cap:
            if time.monotonic() > deadline:
                return kids, f"시간 한도로 자식을 {len(kids)}개까지만 읽음"
            kids.append(kid)
            kid = walker.GetNextSiblingElement(kid)
    except Exception as exc:
        return kids, f"자식 읽기 실패: {exc!r}"[:200]
    return kids, (f"자식 {cap}개 제한" if kid else None)


def has_child(walker, el) -> bool:
    try:
        return bool(walker.GetFirstChildElement(el))
    except Exception:
        return False


def label(node: dict) -> str:
    text = str(node.get("type"))
    if node.get("class"):
        text += f'[{node["class"]}]'
    if node.get("automation_id"):
        text += f'#{node["automation_id"]}'
    return text


def walk_tree(uia, root_el, section: str, limits: Limits, masker: Masker, index: TextIndex,
              seen_hwnds: set[str]) -> tuple[dict | None, dict]:
    """RawView 깊이 우선. 노드 수·깊이·시간 한도에서 멈춘다."""
    started = time.monotonic()
    deadline = started + limits.seconds
    stats = {"nodes": 0, "with_text": 0, "max_depth": 0, "truncated": None}
    types: Counter = Counter()
    root = None
    stack = [(root_el, 0, None, "")]
    while stack:
        if stats["nodes"] >= limits.nodes:
            stats["truncated"] = f"노드 {limits.nodes}개 제한"
            break
        if time.monotonic() > deadline:
            stats["truncated"] = f"{limits.seconds:g}초 제한"
            break
        el, depth, parent, parent_path = stack.pop()
        info, texts = read_node(uia, el)
        stats["nodes"] += 1
        stats["max_depth"] = max(stats["max_depth"], depth)
        types[str(info["type"])] += 1
        if info["hwnd"]:
            seen_hwnds.add(info["hwnd"])
        path = f"{parent_path} > {label(info)}" if parent_path else label(info)
        if texts:
            stats["with_text"] += 1
            index.add(section, path, texts)
        node = {k: v for k, v in info.items() if v is not None and v != [] and v != ""}
        node.update({k: masker(v) for k, v in texts.items()})
        if parent is None:
            root = node
        else:
            parent.setdefault("children", []).append(node)
        if depth >= limits.depth:
            if has_child(uia.walker, el):
                node["children_cut"] = True
            continue
        kids, problem = children(uia.walker, el, deadline)
        if problem:
            node.setdefault("errors", []).append(problem)
            stats["truncated"] = stats["truncated"] or problem
        for kid in reversed(kids):
            stack.append((kid, depth + 1, node, path))
    stats["seconds"] = round(time.monotonic() - started, 2)
    stats["types"] = dict(types.most_common())
    return root, stats


def tree_lines(root: dict | None, limit: int) -> list[str]:
    lines: list[str] = []
    stack = [(root, 0)] if root else []
    while stack and len(lines) < limit:
        node, depth = stack.pop()
        parts = [label(node)]
        shown = set()
        for key in TEXT_FIELDS:
            val = node.get(key)
            if val and val not in shown:
                parts.append(f'{key}="{one_line(val)}"')
                shown.add(val)
        role = node.get("legacy_role")
        if role is not None:
            parts.append(f"role={MSAA_ROLES.get(role, role)}")
        if node.get("children_cut"):
            parts.append("(아래 생략: 깊이 제한)")
        lines.append("  " * depth + " ".join(parts))
        for child in reversed(node.get("children", [])):
            stack.append((child, depth + 1))
    return lines


# ── 판정 ────────────────────────────────────────────────────────────────────

def judge(expects: list[str], index: TextIndex, masker: Masker,
          truncated: bool = False) -> tuple[str, list[dict]]:
    results = []
    for needle in expects:
        hits = index.find(needle)
        results.append({
            "expect": masker(needle), "count": len(hits),
            "sections": sorted({h.section for h in hits}),
            "hits": [{"section": h.section, "path": h.path, "field": h.field,
                      "text": masker(h.text)} for h in hits[:20]],
        })
    in_uia = [any(s.startswith("uia") for s in r["sections"]) for r in results]
    if not expects:
        summary = "판정 보류 — --expect 로 화면에 보이는 메시지 일부를 주면 어디서 나오는지 찾습니다."
    elif all(in_uia):
        summary = ("가능 — UI Automation 에서 메시지 글이 나옵니다. "
                   "맥처럼 창을 건드리지 않고 읽는 방식으로 만들 수 있습니다.")
    elif all(r["count"] for r in results):
        summary = ("부분 가능 — UIA 에는 없지만 Win32 글(창 제목·WM_GETTEXT)에서 찾았습니다. "
                   "이 경로로 만들 수 있는지 봅니다.")
    elif any(r["count"] for r in results):
        summary = "일부만 찾음 — 찾은 문구와 못 찾은 문구가 섞여 있습니다. 보고서를 보고 판단합니다."
    else:
        summary = ("못 찾음 — 지금 상태로는 메시지 글이 UIA·Win32 어디에도 나오지 않습니다. "
                   "(1) 그 문구가 방 창 화면에 지금 보이는지 확인하고, "
                   "(2) NVDA 같은 스크린리더를 켠 채 카카오톡을 다시 켜고 재확인하고, "
                   "(3) 그래도 없으면 클립보드 복사 방식으로 갑니다.")
    if truncated and expects and not all(in_uia):
        summary += (" 단, UIA 트리를 끝까지 읽지 못했습니다 — --max-nodes·--max-seconds 를 늘려 "
                    "다시 확인하세요.")
    return summary, results


# ── 명령 ────────────────────────────────────────────────────────────────────

def cmd_windows(w, args) -> int:
    rows = [window_info(w, h) for h in kakao_windows(w, include_hidden=args.all)]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print("카카오톡 창이 없습니다 — 카카오톡이 켜져 있는지, 방이 별도 창으로 열려 있는지 보세요.")
        return 0
    print(f"카카오톡 창 {len(rows)}개" + ("" if args.all else " (숨은 창까지 보려면 --all)"))
    for r in rows:
        flags = (" [최소화]" if r["minimized"] else "") + ("" if r["visible"] else " [숨김]")
        print(f'  {r["hwnd"]:>12}  "{r["title"]}"  class={r["class"]}{flags}')
    return 0


def cmd_probe(w, args, make_uia=None) -> int:
    make_uia = make_uia or UIA
    target = resolve_target(w, args.room, args.hwnd)
    masker = Masker(args.full)
    index = TextIndex()
    limits = Limits(args.depth, args.max_nodes, args.max_seconds)

    exe = w.exe_path(w.pid(target))
    env = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "kakaotalk_exe": exe_name(exe) or None,     # 전체 경로는 사용자 이름이 섞일 수 있어 뺀다
        "kakaotalk_version": w.file_version(exe) if exe else None,
        "screen_reader_flag": w.screen_reader(),
    }
    target_info = window_info(w, target)
    flag = {True: "켜짐", False: "꺼짐", None: "모름"}[env["screen_reader_flag"]]
    print(f'[방 창] "{target_info["title"]}"  hwnd={target_info["hwnd"]}  class={target_info["class"]}')
    print(f'[환경] {env["os"]} · 카카오톡 {env["kakaotalk_version"] or "버전 모름"} · '
          f"스크린리더 플래그 {flag}")
    if target_info["minimized"]:
        print("  ! 창이 최소화돼 있습니다 — 최소화를 풀고 다시 실행하는 게 좋습니다.")

    win32 = win32_children(w, target, masker, index)
    print(f"\n[Win32 자식 창] {len(win32)}개")
    for row in win32[:WIN32_PRINT_MAX]:
        extra = " ".join(f'{key}="{one_line(row[key])}"' for key in ("title", "wm_gettext")
                         if row.get(key))
        print("  " + "  " * (row["depth"] - 1) + f'{row["class"]}  {row["hwnd"]}'
              + ("" if row["visible"] else " [숨김]") + (f"  {extra}" if extra else ""))
    if len(win32) > WIN32_PRINT_MAX:
        print(f"  … {len(win32) - WIN32_PRINT_MAX}개 더 (보고서에)")

    print("\nUIA 트리를 읽는 중… (노드가 많으면 수십 초 걸릴 수 있습니다)", file=sys.stderr)
    uia_report: dict = {}
    try:
        uia = make_uia()
    except Exception as exc:
        uia_report = {"error": f"UIA 를 열지 못했습니다: {exc!r}"}
    else:
        seen: set[str] = set()
        try:
            tree, stats = walk_tree(uia, uia.element(target), "uia", limits, masker, index, seen)
            uia_report = {"stats": stats, "tree": tree}
        except Exception as exc:
            uia_report = {"error": f"UIA 트리를 읽지 못했습니다: {exc!r}"}
        # 목록처럼 보이는 Win32 창이 위 트리에 안 나왔으면 그 창에서 따로 걸어 본다
        extras = []
        for row in win32:
            if "list" not in row["class"].lower() or row["hwnd"] in seen:
                continue
            try:
                sub, sub_stats = walk_tree(uia, uia.element(int(row["hwnd"], 16)), "uia-extra",
                                           limits, masker, index, seen)
                extras.append({"hwnd": row["hwnd"], "class": row["class"], "stats": sub_stats,
                               "tree": sub})
            except Exception as exc:
                extras.append({"hwnd": row["hwnd"], "class": row["class"], "error": repr(exc)})
        if extras:
            uia_report["extra_subtrees"] = extras

    if "error" in uia_report:
        print(f"\n[UIA] {uia_report['error']}")
    else:
        s = uia_report["stats"]
        print(f'\n[UIA] 노드 {s["nodes"]}개 · 글 있는 노드 {s["with_text"]}개 · '
              f'최대 깊이 {s["max_depth"]} · {s["seconds"]}초'
              + (f' · 중단: {s["truncated"]}' if s["truncated"] else ""))
        print("  종류: " + ", ".join(f"{k} {v}" for k, v in s["types"].items()))
        lines = tree_lines(uia_report["tree"], args.print_lines)
        print(f"  트리 앞부분 {len(lines)}줄 (전체는 보고서에):")
        for line in lines:
            print("    " + line)
    for extra in uia_report.get("extra_subtrees", []):
        if "error" in extra:
            print(f'\n[UIA 따로 읽은 창] {extra["class"]} {extra["hwnd"]}: {extra["error"]}')
            continue
        s = extra["stats"]
        print(f'\n[UIA 따로 읽은 창] {extra["class"]} {extra["hwnd"]} — 노드 {s["nodes"]}개 · '
              f'글 있는 노드 {s["with_text"]}개')
        for line in tree_lines(extra["tree"], min(args.print_lines, 40)):
            print("    " + line)

    walks = [uia_report] + uia_report.get("extra_subtrees", [])
    truncated = "error" in uia_report or any(
        (x.get("stats") or {}).get("truncated") for x in walks)
    summary, results = judge(args.expect, index, masker, truncated)
    print("\n[판정]")
    for r in results:
        where = ", ".join(r["sections"]) if r["count"] else "-"
        print(f'  {"찾음" if r["count"] else "못 찾음"}: "{r["expect"]}"  ({r["count"]}곳: {where})')
        for hit in r["hits"][:5]:
            print(f'    {hit["section"]}: {hit["path"]}  ({hit["field"]})')
    print(f"  → {summary}")

    report = {
        "probe": "kakao_win_probe", "version": 1, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "masked": not args.full, "env": env, "target": target_info,
        "win32_children": win32, "uia": uia_report,
        "verdict": {"summary": summary, "expects": results},
    }
    with open(args.out, "w", encoding="utf-8", errors="replace") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    note = "긴 글은 앞 8자만 들어 있습니다" if not args.full else "글 전체가 들어 있습니다"
    print(f"\n[보고서] {args.out} — {note}. Claude 에게 보여 주되 공개 저장소에는 올리지 마세요.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="윈도우 카카오톡 읽기 확인 (읽기 전용)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("windows", help="열린 카카오톡 창 목록")
    p.add_argument("--all", action="store_true", help="숨은 창도 보기")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("probe", help="방 창을 덤프하고 --expect 문구를 찾는다")
    p.add_argument("room", nargs="?", help="방 창 제목 (windows 명령으로 확인)")
    p.add_argument("--hwnd", help="창 핸들 — 제목이 같은 창이 여럿일 때 (예: 0x1A2B3C)")
    p.add_argument("--expect", action="append", default=[], metavar="문구",
                   help="방 창 화면에 지금 보이는 메시지 일부 (여러 번 줄 수 있다)")
    p.add_argument("--depth", type=int, default=30, help="UIA 트리 최대 깊이 (기본 30)")
    p.add_argument("--max-nodes", type=int, default=4000, help="UIA 노드 최대 개수 (기본 4000)")
    p.add_argument("--max-seconds", type=float, default=90, help="UIA 트리 읽기 시간 한도 (기본 90초)")
    p.add_argument("--out", default=DEFAULT_REPORT, help=f"보고서 경로 (기본 {DEFAULT_REPORT})")
    p.add_argument("--full", action="store_true", help="보고서에 글 전체를 남긴다 (기본: 긴 글은 앞 8자만)")
    p.add_argument("--print-lines", type=int, default=150, help="화면에 찍을 트리 줄 수 (기본 150)")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("Windows 전용입니다. macOS 는 kakao-read 스킬(kakao_read.py)을 쓰세요.", file=sys.stderr)
        return 2
    try:
        w = Win32()
        return cmd_windows(w, args) if args.cmd == "windows" else cmd_probe(w, args)
    except ProbeError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
