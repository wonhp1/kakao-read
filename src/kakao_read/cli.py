"""kakao-read 명령.

    kakao-read doctor                점검 (엔진 설치·로그인·설정·방 창)
    kakao-read login-help            Claude Code 로그인 방법
    kakao-read windows               열린 카카오톡 창 제목
    kakao-read read "방" [-n 20]     방의 최근 메시지
    kakao-read ask "방" ["질문"]      Claude 답 미리보기 (보내지 않는다)
    kakao-read run [--once]          봇 실행 — config.toml 의 dry_run·allowed_send 를 따른다
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from kakao_read import __version__
from kakao_read.backends import BackendError, BackendUnavailable, ChatMessage, get_backend
from kakao_read.bot import Bot, build_system, build_user, tidy
from kakao_read.config import DEFAULT_PATH, Config, ConfigError, load
from kakao_read.engine import LOGIN_GUIDE, ClaudeCli, EngineError, auth_status, find_claude, install_hint, version


def _engine(cfg: Config) -> ClaudeCli:
    return ClaudeCli(cfg.model, cfg.timeout_seconds, cfg.claude_path)


def _stamp(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def cmd_doctor(args) -> int:
    ok = True

    def line(good: bool | None, text: str) -> None:
        nonlocal ok
        mark = {True: "✅", False: "❌", None: "⚠️ "}[good]
        if good is False:
            ok = False
        print(f"{mark} {text}")

    print(f"kakao-read {__version__} · {sys.platform}")
    cfg = None
    try:
        cfg = load(args.config)
        line(True, f"설정 파일: {args.config}")
    except ConfigError as exc:
        line(False, str(exc))

    binary = find_claude(cfg.claude_path if cfg else "")
    if binary is None:
        line(False, install_hint())
    else:
        line(True, f"Claude Code: {version(binary) or '버전 확인 실패'} ({binary})")
        logged, detail = auth_status(binary)
        line(logged, f"Claude 로그인: {detail}" + ("" if logged else " — `kakao-read login-help` 를 보세요"))

    try:
        backend = get_backend()
        titles = [t for t, _ in backend.windows()]
        line(True, f"카카오톡 창 읽기: {backend.name} — 열린 창 {len(titles)}개")
    except BackendUnavailable as exc:
        line(False, str(exc))
        titles = None
    except BackendError as exc:
        line(False, f"카카오톡 창을 읽지 못했습니다: {exc}")
        titles = None

    if sys.platform == "darwin":
        from kakao_read.backends.mac_ax import bot_running
        if bot_running():
            line(None, "kakaobot 봇이 돌고 있습니다 — 같은 방을 둘이 함께 맡으면 답이 겹칩니다")

    if cfg:
        if not cfg.watch:
            line(False, "rooms.watch 가 비어 있습니다 — 답할 방의 창 제목을 넣으세요 (`kakao-read windows` 로 확인)")
        for room in cfg.watch:
            found = None if titles is None else room in titles
            where = "" if found is None else (" — 창 열려 있음" if found else " — 창이 없습니다. 방을 더블클릭해 창으로 여세요")
            send = "실제 전송" if cfg.can_send(room) else ("미리보기(dry_run)" if cfg.dry_run else "미리보기(allowed_send 에 없음)")
            line(None if found is None else found, f"방 '{room}': {send}{where}")
        line(None if cfg.dry_run else True,
             "dry_run = true — 답을 화면에만 보여 줍니다. 자동 전송은 config.toml 에서 false 로" if cfg.dry_run
             else f"자동 전송 켜짐 — 보내는 방: {', '.join(cfg.allowed_send) or '(없음)'}")
        _, warnings = cfg.knowledge()
        for w in warnings:
            line(None, w)
    return 0 if ok else 1


def cmd_login_help(args) -> int:
    binary = find_claude()
    if binary is None:
        print(install_hint() + "\n")
    else:
        logged, detail = auth_status(binary)
        print(f"지금 상태: {detail}\n")
    print(LOGIN_GUIDE)
    return 0


def cmd_windows(args) -> int:
    for title, state in get_backend().windows():
        print((title or "(제목 없음 — 스레드 창 등)") + ("" if state == "보임" else f"  ({state})"))
    return 0


def _show(m: ChatMessage) -> str:
    who = "공지" if m.system else ("나" if m.outgoing else (m.sender or "?"))
    return f"[{who}]{' [스레드 댓글]' if m.thread_comment else ''} {m.text}" + (f"  ({m.time})" if m.time else "")


def cmd_read(args) -> int:
    snap = get_backend().read(args.room, args.count)
    if snap.scrolled_up:
        print("⚠️ 방 창이 위로 스크롤돼 있어 최근 메시지가 아닐 수 있습니다", file=sys.stderr)
    for m in snap.messages:
        print(_show(m))
    return 0


def cmd_ask(args) -> int:
    """방의 최근 대화를 맥락으로 Claude 답을 만들어 보여 준다. 보내지 않는다."""
    cfg = load(args.config)
    snap = get_backend().read(args.room, cfg.read_count)
    msgs = snap.messages
    if args.question:
        group = [ChatMessage(args.question, "질문자", False)]
        context = msgs[-cfg.context_messages:]
    else:
        idx = next((i for i in range(len(msgs) - 1, -1, -1)
                    if not (msgs[i].outgoing or msgs[i].system or msgs[i].thread_comment)), None)
        if idx is None:
            print("답할 받은 메시지가 화면에 없습니다 — 질문을 직접 넣어 보세요: kakao-read ask \"방\" \"질문\"")
            return 1
        group, context = [msgs[idx]], msgs[max(0, idx - cfg.context_messages):idx]
    print("질문: " + " / ".join(m.text for m in group))
    knowledge, warnings = cfg.knowledge()
    for w in warnings:
        print(f"⚠️ {w}")
    raw = _engine(cfg).complete(build_system(cfg, knowledge), build_user(args.room, context, group))
    text = tidy(raw, cfg)
    print("답(보내지 않음): " + (text if text is not None else "(답하지 않음 — AI 가 SKIP)"))
    return 0


def cmd_run(args) -> int:
    cfg = load(args.config)
    if not cfg.watch:
        raise ConfigError("rooms.watch 가 비어 있습니다 — 답할 방의 창 제목을 넣으세요")
    binary = find_claude(cfg.claude_path)
    if binary is None:
        raise EngineError(install_hint())
    logged, _ = auth_status(binary)
    if not logged:
        raise EngineError("Claude Code 로그인이 필요합니다.\n\n" + LOGIN_GUIDE)
    backend = get_backend()
    print(f"kakao-read {__version__} — {backend.name} · 모델 {cfg.model} · {cfg.poll_seconds:g}초마다 · "
          f"답하는 조건: {'모든 메시지(AI 가 고름)' if cfg.trigger == 'all' else '낱말 ' + ', '.join(cfg.keywords)}")
    for room in cfg.watch:
        print(f"  · {room}: " + ("실제 전송" if cfg.can_send(room) else "미리보기만"))
    print("멈추려면 Ctrl+C", flush=True)
    bot = Bot(cfg, backend, _engine(cfg), log=_stamp)
    try:
        bot.run(once=args.once)
    except KeyboardInterrupt:
        print("\n멈췄습니다")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kakao-read", description="카카오톡 PC 방을 읽고 Claude 가 답장")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="점검")
    sub.add_parser("login-help", help="Claude Code 로그인 방법")
    sub.add_parser("windows", help="열린 카카오톡 창 제목")
    p = sub.add_parser("read", help="방의 최근 메시지")
    p.add_argument("room")
    p.add_argument("-n", "--count", type=int, default=20)
    p = sub.add_parser("ask", help="Claude 답 미리보기 (보내지 않음)")
    p.add_argument("room")
    p.add_argument("question", nargs="?", help="비우면 방의 마지막 받은 메시지")
    p = sub.add_parser("run", help="봇 실행")
    p.add_argument("--once", action="store_true", help="한 바퀴만 돌고 끝낸다")
    for s in sub.choices.values():
        s.add_argument("--config", type=Path, default=DEFAULT_PATH, help="설정 파일 (기본 config.toml)")
    args = ap.parse_args(argv)
    handlers = {"doctor": cmd_doctor, "login-help": cmd_login_help, "windows": cmd_windows,
                "read": cmd_read, "ask": cmd_ask, "run": cmd_run}
    try:
        return handlers[args.cmd](args)
    except (ConfigError, EngineError, BackendError, BackendUnavailable) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
