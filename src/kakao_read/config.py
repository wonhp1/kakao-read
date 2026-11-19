"""config.toml 읽기. 빠진 값은 기본값으로 채우고, 틀린 값은 시작할 때 바로 알린다."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("config.toml")
TRIGGERS = ("keyword", "all")
MODES = ("command", "auto")   # command: 내가 명령할 때만 (kakao-read reply) · auto: run 이 알아서
MAX_KNOWLEDGE_CHARS = 60_000   # 자료가 이보다 길면 앞부분만 쓴다 (매 답마다 통째로 보낸다)


class ConfigError(Exception):
    pass


@dataclass
class Config:
    watch: list[str] = field(default_factory=list)
    allowed_send: list[str] = field(default_factory=list)
    dry_run: bool = True
    mode: str = "command"
    trigger: str = "keyword"
    keywords: list[str] = field(default_factory=lambda: ["!ai"])
    poll_seconds: float = 10.0
    read_count: int = 30
    context_messages: int = 20
    max_per_minute: int = 5
    max_chars: int = 800
    prefix: str = ""   # 답 앞에 붙일 표시 (기본 없음 — 내가 직접 쓴 것처럼 보낸다)
    model: str = "haiku"
    timeout_seconds: int = 120
    claude_path: str = ""
    persona: str = ""
    knowledge_files: list[str] = field(default_factory=list)
    learn_history: bool = True        # 처음 보는 방은 지난 대화를 모두 읽어 프로필(맥락·말투)을 만든다
    history_max: int = 2000           # 그때 읽을 최대 메시지 수
    profile_dir: str = "profiles"     # 프로필 저장 폴더 (설정 파일 기준 상대 경로도 된다)
    path: Path | None = None

    def profile_folder(self) -> Path:
        p = Path(self.profile_dir).expanduser()
        if not p.is_absolute():
            p = (self.path.parent if self.path else Path.cwd()) / p
        return p

    def can_send(self, room: str) -> bool:
        return not self.dry_run and room in self.allowed_send

    def knowledge(self) -> tuple[str, list[str]]:
        """(자료 본문, 경고). 자료 파일은 설정 파일 기준 상대 경로도 된다."""
        base = self.path.parent if self.path else Path.cwd()
        parts, warnings = [], []
        for raw in self.knowledge_files:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = base / p
            try:
                parts.append(f"## {p.name}\n{p.read_text(encoding='utf-8').strip()}")
            except OSError as exc:
                warnings.append(f"자료 파일을 읽지 못했습니다: {p} ({exc.strerror or exc})")
        text = "\n\n".join(parts)
        if len(text) > MAX_KNOWLEDGE_CHARS:
            warnings.append(f"자료가 {len(text):,}자라 앞 {MAX_KNOWLEDGE_CHARS:,}자만 씁니다")
            text = text[:MAX_KNOWLEDGE_CHARS]
        return text, warnings


def _list(section: dict, key: str, default: list[str]) -> list[str]:
    value = section.get(key, default)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"'{key}' 는 글 목록이어야 합니다 (예: [\"방 이름\"])")
    return list(value)


def load(path: Path = DEFAULT_PATH) -> Config:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"설정 파일이 없습니다: {path} — config.example.toml 을 복사해 만드세요 "
                          "(scripts/setup 이 만들어 줍니다)") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"설정 파일 형식이 틀렸습니다: {path} — {exc}") from exc

    rooms, reply, ai = data.get("rooms", {}), data.get("reply", {}), data.get("ai", {})
    d = Config()
    cfg = Config(
        watch=_list(rooms, "watch", d.watch),
        allowed_send=_list(rooms, "allowed_send", d.allowed_send),
        dry_run=bool(reply.get("dry_run", d.dry_run)),
        mode=str(reply.get("mode", d.mode)),
        trigger=str(reply.get("trigger", d.trigger)),
        keywords=_list(reply, "keywords", d.keywords),
        poll_seconds=float(reply.get("poll_seconds", d.poll_seconds)),
        read_count=int(reply.get("read_count", d.read_count)),
        context_messages=int(reply.get("context_messages", d.context_messages)),
        max_per_minute=int(reply.get("max_per_minute", d.max_per_minute)),
        max_chars=int(reply.get("max_chars", d.max_chars)),
        prefix=str(reply.get("prefix", d.prefix)),
        model=str(ai.get("model", d.model)),
        timeout_seconds=int(ai.get("timeout_seconds", d.timeout_seconds)),
        claude_path=str(ai.get("claude_path", d.claude_path)),
        persona=str(ai.get("persona", d.persona)).strip(),
        knowledge_files=_list(ai, "knowledge_files", d.knowledge_files),
        learn_history=bool(ai.get("learn_history", d.learn_history)),
        history_max=int(ai.get("history_max", d.history_max)),
        profile_dir=str(ai.get("profile_dir", d.profile_dir)),
        path=path,
    )
    if cfg.history_max < 20:
        raise ConfigError("ai.history_max 는 20 이상이어야 합니다")
    if cfg.mode not in MODES:
        raise ConfigError(f"reply.mode 는 command(명령할 때만) / auto(자동 답장) 중 하나여야 합니다 (지금: {cfg.mode!r})")
    if cfg.trigger not in TRIGGERS:
        raise ConfigError(f"reply.trigger 는 {' / '.join(TRIGGERS)} 중 하나여야 합니다 (지금: {cfg.trigger!r})")
    if cfg.trigger == "keyword" and not any(k.strip() for k in cfg.keywords):
        raise ConfigError("trigger = \"keyword\" 이면 reply.keywords 에 낱말을 하나 이상 넣어야 합니다")
    if cfg.poll_seconds < 3:
        raise ConfigError("reply.poll_seconds 는 3초 이상이어야 합니다 (카카오톡이 버벅입니다)")
    if cfg.read_count < 5:
        raise ConfigError("reply.read_count 는 5 이상이어야 합니다")
    if cfg.max_per_minute < 1 or cfg.max_chars < 20:
        raise ConfigError("reply.max_per_minute 는 1 이상, reply.max_chars 는 20 이상이어야 합니다")
    unknown = [r for r in cfg.allowed_send if r not in cfg.watch]
    if unknown:
        raise ConfigError(f"rooms.allowed_send 의 방은 rooms.watch 에도 있어야 합니다: {', '.join(unknown)}")
    return cfg
