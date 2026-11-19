"""답을 만드는 엔진 — 설치된 Claude Code CLI(`claude -p`)를 도구 없이 부른다. API 키가 필요 없다.

오픈채팅의 낯선 사람 글이 그대로 들어가므로, Claude 가 파일·명령·웹·MCP 를 쓸 수 없게 막는다.
  --safe-mode                 개인 CLAUDE.md·메모리·플러그인을 불러오지 않는다 (로그인은 그대로)
  --tools ""                  기본 도구를 모두 뺀다 (--allowedTools 는 자동 승인 규칙이라 막지 못한다)
  --strict-mcp-config ...     MCP 서버를 비운다
  --disable-slash-commands    스킬·명령을 끈다
  --no-session-persistence    대화를 저장하지 않는다
  --system-prompt             기본 코딩 지시 대신 이 도구의 답장 규칙만 쓴다
대화 내용은 명령행이 아니라 표준입력으로 넘긴다. 옵션이 없는 옛 CLI 면 격리를 풀지 않고 오류로 멈춘다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INSTALL_GUIDE_MAC = "curl -fsSL https://claude.ai/install.sh | bash"
INSTALL_GUIDE_WIN = "irm https://claude.ai/install.ps1 | iex"

LOGIN_GUIDE = """\
Claude Code 로그인 방법 (한 번만 하면 됩니다)

  1) 터미널(윈도우는 PowerShell)에 아래를 입력하고 Enter.
       claude auth login
     · Claude Pro·Max 구독이 있으면 그대로 진행합니다 (보통 이쪽).
     · 구독 없이 API 요금으로 쓰려면 대신:  claude auth login --console

  2) 브라우저가 열리면 claude.ai 에서 쓰는 계정으로 로그인하고 '승인(Authorize)'을 누릅니다.
     · 브라우저가 안 열리면 터미널에 나온 주소를 복사해 브라우저 주소창에 붙여 넣습니다.
     · 브라우저가 코드를 보여 주면 그 코드를 복사해 터미널에 붙여 넣고 Enter.

  3) 터미널에 로그인 완료 문구가 나오면 끝입니다. 확인하려면:
       claude auth status        → "loggedIn": true 이면 성공

  ※ 이 도구는 Claude 에 파일·명령 실행 권한을 주지 않고 답장 글만 받아 옵니다.
  ※ 비밀번호·코드를 채팅이나 설정 파일에 적지 마세요. 로그인 정보는 Claude Code 가 따로 보관합니다.
"""


class EngineError(Exception):
    pass


def find_claude(path: str = "") -> str | None:
    """설정한 경로 → PATH → 설치 스크립트가 두는 곳 순으로 찾는다 (메뉴·작업 스케줄러에서 띄우면 PATH 가 짧다)."""
    if path:
        p = Path(path).expanduser()
        return str(p) if p.exists() else None
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    names = ["claude.exe", "claude.cmd"] if sys.platform == "win32" else ["claude"]
    dirs = [home / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    for d in dirs:
        for n in names:
            if (d / n).exists():
                return str(d / n)
    return None


def install_hint() -> str:
    cmd = INSTALL_GUIDE_WIN if sys.platform == "win32" else INSTALL_GUIDE_MAC
    script = "scripts\\setup.ps1" if sys.platform == "win32" else "scripts/setup.sh"
    return f"Claude Code 가 설치돼 있지 않습니다 — {script} 를 실행하거나 직접 설치하세요:\n  {cmd}"


def version(binary: str) -> str | None:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def auth_status(binary: str) -> tuple[bool, str]:
    """(로그인 됐나, 설명). 계정 이메일 같은 정보는 화면에 내지 않는다."""
    try:
        out = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"로그인 상태를 확인하지 못했습니다: {exc}"
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if out.returncode == 0 and data.get("loggedIn", True):
        method = data.get("authMethod") or ""
        sub = data.get("subscriptionType") or ""
        return True, "로그인 됨" + (f" ({method}{', ' + sub if sub else ''})" if method else "")
    return False, "로그인 안 됨"


class ClaudeCli:
    def __init__(self, model: str = "haiku", timeout: int = 120, path: str = "") -> None:
        self.model = model
        self.timeout = timeout
        self.path = path

    def argv(self, binary: str, system: str) -> list[str]:
        return [
            binary, "-p",
            "--safe-mode",
            "--tools", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disallowedTools", "mcp__*",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--model", self.model,
            "--system-prompt", system,
        ]

    def complete(self, system: str, user: str) -> str:
        binary = find_claude(self.path)
        if binary is None:
            raise EngineError(install_hint())
        try:
            proc = subprocess.run(
                self.argv(binary, system), input=user, capture_output=True, text=True,
                encoding="utf-8", timeout=self.timeout,
                cwd=tempfile.gettempdir(),   # 이 저장소·설정 파일 밖에서 돈다
                env={**os.environ, "NO_COLOR": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"Claude 가 {self.timeout}초 안에 답하지 않았습니다") from exc
        except OSError as exc:
            raise EngineError(f"claude 를 실행하지 못했습니다: {exc}") from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            low = err.lower()
            if "login" in low or "auth" in low or "로그인" in err:
                raise EngineError("Claude Code 로그인이 필요합니다 — `kakao-read login-help` 를 보세요\n" + err[:300])
            if "unknown option" in low or "unrecognized" in low:
                raise EngineError("설치된 Claude Code 가 오래돼 격리 옵션을 모릅니다 — `claude update` 후 다시 하세요\n"
                                  + err[:300])
            raise EngineError(f"claude -p 실패 (종료 코드 {proc.returncode}): {err[:300]}")
        return proc.stdout.strip()
