# kakao-read — AI 에이전트용 안내

사용자가 이 레포 주소를 주고 "설치해 줘", "카톡 답장하게 해 줘"라고 하면 아래 순서대로 진행한다.
사람이 읽는 설명은 README.md 에 있다.

## 지킬 것

- **실제로 메시지를 보내는 명령은 사용자가 그 방·그 내용에 동의했을 때만** 실행한다.
  `send --yes`, `reply --yes`, `reply` 의 확인에 y, `dry_run = false` 로 `run`.
  미리 보려면 `reply "방" --dry` 를 쓰고, 나온 답을 사용자에게 보여 준 뒤 보낸다.
- **로그인은 사용자가 한다.** `claude auth login` 을 사용자의 터미널에 띄우고, 브라우저 승인이 끝날 때까지 기다린다.
  비밀번호·인증 코드를 대신 입력하지 않는다.
- 대화 내용, `profiles/`, `config.toml`, `probe-report*.json` 은 개인정보다. 밖으로 보내거나 커밋하지 않는다 (.gitignore 에 있다).
- 카카오톡 창을 앞으로 가져오거나 마우스·키보드를 실제로 움직이지 않는다. 읽기·보내기는 이 도구가 창 메시지로 한다.

## 1. 운영체제 확인 → 설치

설치 전에 사용자에게 **답장 방식**을 묻는다.

- `command` (기본) — 사용자가 명령할 때만 답한다 (`kakao-read reply "방"`).
- `auto` — `kakao-read run` 을 켜 두면 새 메시지에 알아서 답한다.

| 운영체제 | 확인 | 설치 (레포 루트에서) |
|---|---|---|
| macOS | `uname` 이 `Darwin` | `bash scripts/setup.sh --mode <command\|auto> --no-login` |
| Windows | PowerShell, `$env:OS` 가 `Windows_NT` | `powershell -ExecutionPolicy ByPass -File scripts\setup.ps1 -Mode <command\|auto> -NoLogin` |
| 그 밖 | — | 지원하지 않는다고 알린다 |

설치 스크립트는 uv, Claude Code, Python 패키지를 설치하고 `config.toml` 을 만든다. 여러 번 돌려도 된다.
`-NoLogin`/`--no-login` 은 로그인 창을 띄우지 않고 상태만 알려 준다 (에이전트 셸은 입력을 받을 수 없다).
설치 후 uv 가 PATH 에 없으면 `~/.local/bin` (Windows: `%USERPROFILE%\.local\bin`) 을 PATH 앞에 붙인다.

## 2. Claude Code 로그인

`claude auth status` 가 `"loggedIn": true` 가 아니면, 사용자의 터미널에서 `claude auth login` 을 실행하게 하고
"브라우저에서 승인하고, 코드가 나오면 터미널에 붙여 넣어 주세요"라고 안내한 뒤 기다린다.
답장을 만들고 방 프로필을 정리하는 데 필요하다. 읽기(`read`)와 직접 보내기(`send`)에는 필요 없다.

## 3. 카카오톡 방 창

- 사용자에게 카카오톡 목록에서 답할 방을 **더블클릭해 별도 창으로** 열어 달라고 한다. **최소화하지 않는다.**
- macOS 는 시스템 설정 › 개인정보 보호 및 보안 › 손쉬운 사용에서 명령을 실행하는 앱(터미널 등)을 켜야 한다.
- `uv run kakao-read windows` 로 열린 방 창 제목을 확인한다.
- 자동 모드면 그 제목을 `config.toml` 의 `[rooms] watch` 에 넣고, 실제로 보낼 방은 `allowed_send` 에도 넣는다.
- Windows 단톡방은 내 카톡 이름을 `KAKAO_MY_NAME` 환경 변수로 주면 내가 보낸 글을 정확히 가린다 (1:1 방은 필요 없다).

## 4. 확인 — `doctor` 가 안 된 것을 알려 준다

```
uv run kakao-read doctor            # 엔진·로그인·카카오톡(설치·실행·버전)·권한·방 창 점검
uv run kakao-read read "방" -n 10   # 최근 메시지가 읽히는지
```

- `doctor` 끝에 **'다음 할 일'** 이 나오면 사용자에게 하나씩 쉬운 말로 안내하고, 끝냈다고 하면 `doctor` 를 다시 돌린다.
  ❌ 가 없어질 때까지 반복한다. 사용자가 할 일을 대신하지 않는다 (설정 화면 열기·카카오톡 창 열기 등).
- macOS 손쉬운 사용 권한은 `doctor` 가 알려 준 앱을 켠 뒤 **그 앱을 완전히 종료했다가 다시 열어야** 적용된다.
  에이전트가 그 앱 안에서 돌고 있으면 대화가 끊긴다는 것을 미리 말한다.
- 카카오톡 버전이 ⚠️ 이면(실측한 판과 다름) 그대로 써 보되, 읽기가 이상하면 버전 차이일 수 있다고 알린다.
- 준비가 끝나면 `doctor` 가 제안하는 첫 시도(`reply "방" --dry`)를 권한다.

## 5. 쓰기

- 명령 모드: `uv run kakao-read reply "방" --dry` → 답을 사용자에게 보여 주고 동의를 받은 뒤 `reply "방" --yes`.
  요청을 붙일 수 있다: `reply "방" "생일 축하한다고 해줘"`.
- 자동 모드: `uv run kakao-read run` — 처음엔 `dry_run = true` 로 미리보기만 하고, 괜찮다고 하면 `false` 로.
- 처음 보는 방은 첫 `reply`/`run` 때 지난 대화를 모두 읽어 `profiles/<방>.md` (맥락·사용자의 말투)를 만든다.
  방 창을 맨 위까지 올려 과거 메시지를 불러오느라 1~2분 걸린다 (macOS 는 그동안 방 창이 화면에 보여야 한다).
  다시 만들려면 `uv run kakao-read learn "방"`.
- 답 앞에 표시를 붙이지 않는다 (`prefix = ""`). 사용자가 원하면 `prefix = "🤖 "`.

## 문제 해결

| 증상 | 할 일 |
|---|---|
| uv 가 Python 설치 중 `Missing expected target directory` | 설치 스크립트가 3.13 으로 다시 한다. 직접: `uv python install 3.13` → `uv sync --python 3.13` |
| `"방" 방 창을 찾지 못했습니다` | 방을 별도 창으로 열었는지, 제목이 정확한지 (`kakao-read windows`) |
| Windows `대화를 복사하지 못했습니다` | 창이 최소화됐는지. 다른 클립보드 프로그램이 클립보드를 잡고 있지 않은지 |
| Windows 에서 아무것도 안 됨 | 카카오톡을 관리자 권한으로 켰다면 PowerShell 도 관리자 권한으로 |
| 카카오톡 버전이 달라 창 구조가 바뀐 것 같음 | `uv run probe/kakao_win_probe.py probe "방"` 결과를 보고 `src/kakao_read/backends/windows.py` 를 고친다 |

## 개발

- 테스트: `uv run --group dev pytest -q` (실제 카카오톡·Claude 호출 없음)
- 플랫폼별 코드는 `src/kakao_read/backends/` — `mac.py`(접근성), `windows.py`(Win32 창 메시지). 실행 중인 OS 로 자동 선택된다.
- Windows 동작 원리(클립보드 읽기, WM_CHAR + Enter 보내기, 휠 메시지로 과거 대화 불러오기)는 README 의 '윈도우' 절과 `windows.py` 주석에 있다.
