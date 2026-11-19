"""kakao-read 명령.

    kakao-read doctor                점검 (엔진 설치·로그인·설정·방 창)
    kakao-read login-help            Claude Code 로그인 방법
    kakao-read windows               열린 카카오톡 창 제목
    kakao-read read "방" [-n 20]     방의 최근 메시지
    kakao-read send "방" "글" --yes  메시지 하나 보내기 (--yes 없으면 미리보기)
    kakao-read learn "방"            지난 대화를 모두 읽어 방 프로필(맥락·말투)을 새로 만든다
    kakao-read ask "방" ["질문"]      Claude 답 미리보기 (보내지 않는다)
    kakao-read reply "방" ["요청"]    명령 모드: 지금 답장을 써서 확인 후 보낸다 (--yes 바로, --dry 보기만)
    kakao-read run [--once]          자동 모드(mode = "auto"): 새 메시지에 알아서 답한다 — dry_run·allowed_send 를 따른다
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from kakao_read import __version__, profile
from kakao_read.backends import BackendError, BackendUnavailable, ChatMessage, SendUncertain, get_backend
from kakao_read.bot import Bot, build_system, build_user, compose, ensure_profile, tidy
from kakao_read.config import DEFAULT_PATH, Config, ConfigError, load
from kakao_read.engine import LOGIN_GUIDE, ClaudeCli, EngineError, auth_status, find_claude, install_hint, version


def _engine(cfg: Config) -> ClaudeCli:
    return ClaudeCli(cfg.model, cfg.timeout_seconds, cfg.claude_path)


def _stamp(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def cmd_doctor(args) -> int:
    """준비 상태를 차례로 점검하고, 안 된 것은 고치는 법을 '다음 할 일'로 모아 보여 준다."""
    ok = True
    todo: list[str] = []

    def line(good: bool | None, text: str, fix: str = "") -> None:
        nonlocal ok
        mark = {True: "✅", False: "❌", None: "⚠️ "}[good]
        if good is False:
            ok = False
        print(f"{mark} {text}")
        if fix and good is not True:
            todo.append(fix)

    print(f"kakao-read {__version__} · {sys.platform}")
    cfg = None
    try:
        cfg = load(args.config)
        line(True, f"설정 파일: {args.config}")
    except ConfigError as exc:
        line(False, str(exc), "설정 파일 만들기: bash scripts/setup.sh (윈도우는 scripts\\setup.ps1)")

    binary = find_claude(cfg.claude_path if cfg else "")
    if binary is None:
        line(False, "Claude Code 가 설치돼 있지 않습니다", install_hint())
    else:
        line(True, f"Claude Code: {version(binary) or '버전 확인 실패'} ({binary})")
        logged, detail = auth_status(binary)
        line(logged, f"Claude 로그인: {detail}",
             "Claude 로그인: 터미널에서 claude auth login → 브라우저에서 승인 (자세히: uv run kakao-read login-help)")

    titles = None
    try:
        backend = get_backend()
    except BackendUnavailable as exc:
        line(False, str(exc))
        backend = None
    ready = backend is not None
    for good, item, detail, fix in (getattr(backend, "checks", lambda: [])() if backend else []):
        line(good, f"{item}: {detail}", fix)
        ready = ready and good is not False
    if ready:
        try:
            skip = getattr(backend, "non_rooms", set())
            titles = [t for t, _ in backend.windows() if t and t not in skip]
            shown = ", ".join(titles[:10]) + (" …" if len(titles) > 10 else "")
            line(bool(titles), f"열린 방 창 {len(titles)}개" + (f": {shown}" if titles else " — 읽을 방이 없습니다"),
                 "카카오톡 목록에서 답할 방을 더블클릭해 별도 창으로 여세요 (최소화하지 않기)")
        except BackendError as exc:
            line(False, f"카카오톡 창을 읽지 못했습니다: {exc}")

    if sys.platform == "darwin":
        from kakao_read.backends.mac_ax import bot_running
        if bot_running():
            line(None, "kakaobot 봇이 돌고 있습니다 — 같은 방을 둘이 함께 맡으면 답이 겹칩니다")

    mode = cfg.mode if cfg else None
    if mode == "command":
        line(True, '답장 방식: 명령할 때만 — kakao-read reply "방" ["요청"] (자동 답장은 [reply] mode = "auto")')
    elif mode == "auto":
        line(True, "답장 방식: 자동 — kakao-read run 을 켜 두면 새 메시지에 답합니다")
        if not cfg.watch:
            line(False, "rooms.watch 가 비어 있습니다",
                 "config.toml 의 [rooms] watch 에 답할 방의 창 제목을 넣으세요 (위 '열린 방 창' 이름 그대로)")
        for room in cfg.watch:
            found = None if titles is None else room in titles
            where = "" if found is None else (" — 창 열려 있음" if found else " — 창이 없습니다")
            send = "실제 전송" if cfg.can_send(room) else ("미리보기(dry_run)" if cfg.dry_run else "미리보기(allowed_send 에 없음)")
            line(None if found is None else found, f"방 '{room}': {send}{where}",
                 f"카카오톡에서 '{room}' 방을 더블클릭해 별도 창으로 여세요")
        line(None if cfg.dry_run else True,
             "dry_run = true — 답을 화면에만 보여 줍니다. 자동 전송은 config.toml 에서 false 로" if cfg.dry_run
             else f"자동 전송 켜짐 — 보내는 방: {', '.join(cfg.allowed_send) or '(없음)'}")
    if cfg:
        _, warnings = cfg.knowledge()
        for w in warnings:
            line(None, w)

    print()
    if todo:
        print("다음 할 일")
        for i, fix in enumerate(dict.fromkeys(todo), 1):
            print(f"  {i}. {fix}")
        print("하고 나서 다시 점검: uv run kakao-read doctor")
    elif mode == "auto":
        print("준비됐습니다. 해 보기: uv run kakao-read run  (처음엔 dry_run = true 로 미리보기만)")
    else:
        room = titles[0] if titles else "방 이름"
        print(f'준비됐습니다. 해 보기: uv run kakao-read reply "{room}" --dry  (답만 보여 주고 보내지 않음)')
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


def cmd_send(args) -> int:
    """메시지 하나를 직접 보낸다. --yes 가 없으면 무엇을 보낼지만 보여 준다."""
    backend = get_backend()
    if not args.yes:
        print(f'보낼 메시지(아직 안 보냄) → "{args.room}": {args.text}\n실제로 보내려면 --yes')
        return 0
    try:
        print(backend.send(args.room, args.text))
    except SendUncertain as exc:
        print(f"⚠️ {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_reply(args) -> int:
    """명령 모드: 지금 이 방에 답장을 써서 (확인 후) 보낸다. 처음 보는 방이면 먼저 지난 대화로 프로필을 만든다."""
    cfg = load(args.config)
    backend, engine = get_backend(), _engine(cfg)
    knowledge, warnings = cfg.knowledge()
    for w in warnings:
        print(f"⚠️ {w}")
    room_profile = ensure_profile(cfg, backend, engine, args.room, log=print)
    system = build_system(cfg, knowledge, room_profile, commanded=True)
    draft = compose(cfg, backend, engine, args.room, system, args.request)
    if draft.group:
        print("답할 메시지: " + " / ".join(f"[{m.sender or '?'}] {m.text}" for m in draft.group))
    if args.request:
        print(f"요청: {args.request}")
    if draft.text is None:
        print("답: (보내지 않음 — AI 가 답하면 안 되는 내용으로 판단)")
        return 1
    print(f"답: {draft.text}")
    if args.dry:
        print("(--dry: 보내지 않았습니다)")
        return 0
    if not args.yes:
        try:
            answer = input("보낼까요? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes", "ㅇ", "응", "네"):
            print("보내지 않았습니다")
            return 0
    try:
        print(backend.send(args.room, draft.text))
    except SendUncertain as exc:
        print(f"⚠️ {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_learn(args) -> int:
    """지난 대화를 모두 읽어 방 프로필(맥락·말투)을 새로 만든다. 보내지 않는다."""
    cfg = load(args.config)
    backend = get_backend()
    history = getattr(backend, "history", None)
    print(f'"{args.room}" 지난 대화를 읽는 중… (창을 위로 올렸다가 맨 아래로 되돌립니다)', flush=True)
    if history:
        msgs = history(args.room, args.max or cfg.history_max,
                       progress=lambda n: print(f"  {n}개", end="\r", flush=True))
    else:
        msgs = backend.read(args.room, args.max or cfg.history_max).messages
    print(f"\n메시지 {len(msgs)}개를 읽었습니다. Claude 가 정리하는 중…", flush=True)
    text = profile.build(args.room, msgs, _engine(cfg))
    path = profile.path_for(cfg.profile_folder(), args.room)
    profile.save(path, text)
    print(f"\n{text}\n저장: {path}")
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
    room_profile = profile.load(profile.path_for(cfg.profile_folder(), args.room))
    print("프로필: " + ("있음 (맥락·말투 반영)" if room_profile else f'없음 — kakao-read learn "{args.room}" 으로 만들 수 있습니다'))
    raw = _engine(cfg).complete(build_system(cfg, knowledge, room_profile), build_user(args.room, context, group))
    text = tidy(raw, cfg)
    print("답(보내지 않음): " + (text if text is not None else "(답하지 않음 — AI 가 SKIP)"))
    return 0


def cmd_run(args) -> int:
    cfg = load(args.config)
    if cfg.mode != "auto":
        raise ConfigError('지금은 "명령할 때만" 답하는 설정입니다 ([reply] mode = "command").\n'
                          '  답장 하나 보내기:  kakao-read reply "방" ["요청"]\n'
                          '  자동 답장을 쓰려면 config.toml 의 [reply] mode = "auto" 로 바꾸세요.')
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
    p = sub.add_parser("send", help="메시지 하나 보내기 (--yes 로 실제 전송)")
    p.add_argument("room")
    p.add_argument("text")
    p.add_argument("--yes", action="store_true", help="실제로 보낸다")
    p = sub.add_parser("reply", help="명령 모드: 지금 이 방에 답장을 써서 확인 후 보낸다")
    p.add_argument("room")
    p.add_argument("request", nargs="?", help='무엇을 보낼지 요청 (예: "생일 축하한다고 해줘"). 비우면 마지막 받은 메시지에 답')
    p.add_argument("--yes", action="store_true", help="묻지 않고 바로 보낸다")
    p.add_argument("--dry", action="store_true", help="답만 보여 주고 보내지 않는다")
    p = sub.add_parser("learn", help="지난 대화를 모두 읽어 방 프로필(맥락·말투)을 새로 만든다")
    p.add_argument("room")
    p.add_argument("--max", type=int, default=0, help="읽을 최대 메시지 수 (기본: 설정의 ai.history_max)")
    p = sub.add_parser("ask", help="Claude 답 미리보기 (보내지 않음)")
    p.add_argument("room")
    p.add_argument("question", nargs="?", help="비우면 방의 마지막 받은 메시지")
    p = sub.add_parser("run", help="봇 실행")
    p.add_argument("--once", action="store_true", help="한 바퀴만 돌고 끝낸다")
    for s in sub.choices.values():
        s.add_argument("--config", type=Path, default=DEFAULT_PATH, help="설정 파일 (기본 config.toml)")
    args = ap.parse_args(argv)
    handlers = {"doctor": cmd_doctor, "login-help": cmd_login_help, "windows": cmd_windows,
                "read": cmd_read, "send": cmd_send, "reply": cmd_reply, "learn": cmd_learn, "ask": cmd_ask,
                "run": cmd_run}
    try:
        return handlers[args.cmd](args)
    except (ConfigError, EngineError, BackendError, BackendUnavailable) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
