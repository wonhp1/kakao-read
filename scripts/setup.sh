#!/usr/bin/env bash
# kakao-read 설치 (macOS). 여러 번 돌려도 된다 — 있는 것은 건너뛰고, config.toml 은 덮어쓰지 않는다.
#   1. uv · Claude Code 가 없으면 공식 설치 스크립트로 설치
#   2. Claude Code 로그인이 안 돼 있으면 방법을 보여 주고 로그인 창을 띄운다
#   3. 파이썬 패키지 설치, config.toml 만들기, 점검
#   bash scripts/setup.sh --mode command --no-login   # 묻지 않고 (AI 에이전트용)
#     --mode command|auto  답장 방식. 주면 묻지 않고 config.toml 에 적는다
#     --no-login           Claude 로그인 창을 띄우지 않고 상태만 알려 준다
set -euo pipefail
cd "$(dirname "$0")/.."

MODE=""
NO_LOGIN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-}"; shift 2 ;;
    --no-login) NO_LOGIN=1; shift ;;
    *) echo "모르는 옵션: $1 (--mode command|auto, --no-login)"; exit 2 ;;
  esac
done
if [[ -n "$MODE" && "$MODE" != "command" && "$MODE" != "auto" ]]; then
  echo "--mode 는 command 또는 auto 입니다"; exit 2
fi

set_mode() {
  sed -i '' -E "s/^mode = \"(command|auto)\"/mode = \"$1\"/" config.toml
  if [[ "$1" == "auto" ]]; then
    echo "답장 방식: 자동 답장. 처음엔 dry_run = true 라 미리보기만 하고, 실제 전송은 rooms.allowed_send 의 방만 합니다."
  else
    echo "답장 방식: 명령할 때만 (kakao-read reply)."
  fi
}

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if [[ "$(uname)" != "Darwin" ]]; then
  echo "이 스크립트는 macOS 용입니다. 윈도우는 PowerShell 에서 scripts\\setup.ps1 을 실행하세요."
  exit 1
fi
export PATH="$HOME/.local/bin:$PATH"

bold "1/5 uv (파이썬 실행 도구)"
if command -v uv >/dev/null 2>&1; then
  echo "있음: $(uv --version)"
else
  echo "없어서 설치합니다 (https://astral.sh/uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  command -v uv >/dev/null 2>&1 || { echo "uv 설치를 확인하지 못했습니다 — 새 터미널에서 다시 실행하세요"; exit 1; }
fi

bold "2/5 Claude Code (답을 만드는 엔진)"
if command -v claude >/dev/null 2>&1; then
  echo "있음: $(claude --version)"
else
  echo "없어서 설치합니다 (공식 설치 스크립트 https://claude.ai/install.sh — 진행 표시 없이 1~2분 걸릴 수 있습니다)"
  curl -fsSL https://claude.ai/install.sh | bash
  command -v claude >/dev/null 2>&1 || { echo "claude 설치를 확인하지 못했습니다 — 새 터미널에서 다시 실행하세요"; exit 1; }
  echo "설치됨: $(claude --version)"
fi

bold "3/5 Claude Code 로그인"
if claude auth status >/dev/null 2>&1; then
  echo "로그인 돼 있습니다"
else
  cat <<'EOF'
아직 로그인하지 않았습니다. 지금 로그인 창을 띄웁니다.

  · 브라우저가 열리면 claude.ai 에서 쓰는 계정으로 로그인하고 '승인(Authorize)'을 누르세요.
  · Claude Pro·Max 구독이면 그대로 진행합니다. 구독 없이 API 요금으로 쓰려면
    Ctrl+C 로 멈추고 `claude auth login --console` 을 직접 입력하세요.
  · 브라우저가 안 열리면 아래에 나오는 주소를 복사해 브라우저에 붙여 넣으세요.
  · 브라우저가 코드를 보여 주면 복사해서 이 터미널에 붙여 넣고 Enter.
EOF
  if [[ -t 0 && "$NO_LOGIN" == 0 ]]; then
    read -r -p "Enter 를 누르면 로그인을 시작합니다 (건너뛰려면 n): " answer
    if [[ "${answer:-}" != "n" && "${answer:-}" != "N" ]]; then
      claude auth login || true
    fi
  fi
  if claude auth status >/dev/null 2>&1; then
    echo "✅ 로그인 됐습니다"
  else
    echo "⚠️ 아직 로그인 전입니다. 나중에 터미널에서 'claude auth login' 을 실행하세요."
    echo "   자세한 방법: uv run kakao-read login-help"
  fi
fi

bold "4/5 파이썬 패키지"
if ! uv sync --quiet; then
  echo "기본 Python 으로 실패해 Python 3.13 으로 다시 합니다"
  uv python install 3.13
  uv sync --quiet --python 3.13
fi
echo "완료"

bold "5/5 설정 파일"
created=0
if [[ -f config.toml ]]; then
  echo "config.toml 이 이미 있어 그대로 둡니다"
else
  cp config.example.toml config.toml
  created=1
  echo "config.toml 을 만들었습니다 — rooms.watch 에 방 창 제목을 넣으세요"
fi
if [[ -n "$MODE" ]]; then
  set_mode "$MODE"
elif [[ "$created" == 1 ]]; then
  cat <<'EOF'

답장 방식을 고르세요 (나중에 config.toml 의 [reply] mode 로 바꿀 수 있습니다)
  1) 명령할 때만 (기본) — kakao-read reply "방" 을 실행할 때만, 답을 보여 주고 확인받은 뒤 보냅니다
  2) 자동 답장          — kakao-read run 을 켜 두면 새 메시지에 알아서 답합니다
EOF
  choice=""
  if [[ -t 0 ]]; then read -r -p "번호 (Enter = 1): " choice; fi
  if [[ "$choice" == "2" ]]; then set_mode auto; else set_mode command; fi
fi

bold "점검"
uv run --quiet kakao-read doctor || true

cat <<'EOF'

위 점검에서 ❌ 가 있으면 '다음 할 일'을 차례로 하고 다시 점검하세요:  uv run kakao-read doctor
쓰는 법
  (명령 모드) uv run kakao-read reply "방 이름" ["요청"]  → 답을 보여 주고 확인받은 뒤 보냅니다
  (자동 모드) uv run kakao-read run  → 처음엔 dry_run = true 로 미리보기만. 괜찮으면 config.toml 에서
              allowed_send 에 방을 넣고 dry_run = false
EOF
