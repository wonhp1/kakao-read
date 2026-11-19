# kakao-read

카카오톡 PC의 **방 창을 읽고, Claude가 답장을 써서 보내는** 도구입니다.

- 화면 접근성 기능으로 열린 방 창의 글을 읽습니다. 서버 접속·로그인 가로채기·DB 복호화는 하지 않습니다.
- 답은 내 컴퓨터에 설치한 **Claude Code**(`claude`)가 만듭니다. API 키가 없어도 됩니다.
- 처음에는 **미리보기(dry_run)** 로만 돕니다. 실제 전송은 사용자가 고른 방에서만 켜집니다.

| 플랫폼 | 읽기 | AI 답장 보내기 | 상태 |
|---|---|---|---|
| macOS | ✅ 접근성(AX) | ✅ | 읽기·보내기는 카카오톡 26.8에서 실측했습니다. 봇 루프는 가짜 창으로만 시험했습니다 |
| Windows | 확인 중 | 준비 중 | `probe/`로 UI Automation이 되는지 먼저 확인합니다 ([아래](#윈도우)) |

## 동작

```
몇 초마다 방 창 읽기 → 새로 올라온 글 가려내기 → 답할 글 고르기 → Claude에게 묻기 → 보내기(또는 미리보기)
```

- **답하는 글**: 다른 사람이 보낸 글만 답합니다. 내 계정이 보낸 글(봇의 답 포함), 입퇴장 공지,
  '채팅방에 함께 보내기'로 올라온 스레드 댓글에는 답하지 않습니다.
  한 사람이 이어서 보낸 글은 한 질문으로 묶습니다.
- **답하는 조건**(`reply.trigger`)
  - `keyword`(기본): `!ai`처럼 정한 낱말이 들어간 글에만 답합니다.
  - `all`: 받은 글을 모두 Claude에게 보여 주고, 답할 필요가 있을 때만 답합니다.
- **시작할 때 화면에 있던 글에는 답하지 않습니다.** 켠 뒤에 새로 올라온 글에만 답합니다.
- **안전장치**
  - 방마다 1분에 보내는 답의 수에 상한이 있습니다.
  - 같은 사람의 같은 글에는 한 시간 안에 다시 답하지 않습니다.
  - 보냈는지 확인하지 못한 답은 다시 보내지 않습니다. 같은 답이 두 번 나가는 것을 막기 위해서입니다.
  - 입력창에 사람이 쓰던 글이 있으면 덮어쓰지 않습니다. 다음 차례에 다시 해 봅니다.
- **Claude 격리**: 낯선 사람의 글이 그대로 들어가므로 Claude가 파일·명령·웹·MCP 도구를 쓰지 못하게 막습니다.
  개인 설정(CLAUDE.md·메모리·플러그인)도 불러오지 않습니다. 사용한 옵션은 `src/kakao_read/engine.py`에 적어 두었습니다.

## 설치 (macOS)

```bash
git clone https://github.com/wonhp1/kakao-read.git
cd kakao-read
bash scripts/setup.sh
```

`setup.sh`가 하는 일:

1. **uv**와 **Claude Code**가 없으면 공식 설치 스크립트로 설치합니다.
2. **Claude Code에 로그인되어 있지 않으면** 방법을 안내하고 로그인 창을 띄웁니다.
3. 파이썬 패키지를 설치하고 `config.toml`을 만든 다음 점검(`doctor`)을 실행합니다.

여러 번 실행해도 됩니다. 이미 있는 도구는 건너뛰고, `config.toml`은 덮어쓰지 않습니다.

### Claude Code 로그인

로그인은 한 번만 하면 됩니다. 언제든 `uv run kakao-read login-help`로 다시 볼 수 있습니다.

1. 터미널에 `claude auth login`을 입력합니다.
   - Claude Pro·Max 구독이 있으면 그대로 진행합니다.
   - 구독 없이 API 요금으로 쓰려면 `claude auth login --console`을 입력합니다.
2. 브라우저가 열리면 claude.ai에서 쓰는 계정으로 로그인하고 **승인(Authorize)** 을 누릅니다.
   - 브라우저가 열리지 않으면 터미널에 나온 주소를 복사해 브라우저 주소창에 붙여 넣습니다.
   - 브라우저에 코드가 나오면 그 코드를 복사해 터미널에 붙여 넣고 Enter를 누릅니다.
3. `claude auth status`를 실행해 `"loggedIn": true`가 나오면 끝입니다.

### 권한과 창

1. **손쉬운 사용 권한**: 시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 명령을 실행하는 앱(터미널 등)을 켜고, 그 앱을 다시 엽니다.
2. 카카오톡 목록에서 답할 방을 **더블클릭해 별도 창으로** 엽니다.
   - 창이 최소화되어 있거나 ⌘H로 숨겨져 있어도 읽고 보낼 수 있습니다.
   - 다른 데스크탑에 있는 창은 읽을 수만 있고 보낼 수는 없습니다.
   - 닫은 창은 찾지 못합니다.
3. 방 창이 위로 스크롤되어 있으면 맨 아래로 내릴 때까지 기다립니다.

## 쓰는 법

```bash
uv run kakao-read windows                     # 열린 카카오톡 창 제목 → config.toml 의 rooms.watch 에
uv run kakao-read doctor                      # 점검: 엔진 설치·로그인·설정·방 창
uv run kakao-read read "방 이름" -n 20        # 최근 메시지 보기
uv run kakao-read ask "방 이름" "시험 질문"    # Claude 답 미리보기 (보내지 않음)
uv run kakao-read run                         # 봇 실행 (Ctrl+C 로 멈춤)
```

권하는 순서:

1. `config.toml`의 `rooms.watch`에 방 창 제목을 **정확히** 넣습니다.
2. `ai.persona`에 역할과 말투를, `ai.knowledge_files`에 답의 근거가 될 자료(.md·.txt)를 넣습니다.
3. `ask`로 답이 괜찮은지 봅니다.
4. `run`을 `dry_run = true` 상태로 돌립니다. 이때는 답을 화면에만 보여 줍니다.
5. 괜찮으면 `rooms.allowed_send`에 방을 넣고 `dry_run = false`로 바꿉니다. 그러면 **확인 없이 바로 보냅니다.**

> 이 컴퓨터의 카카오톡 계정이 직접 쓴 글에는 답하지 않습니다. 실제로 시험하려면 다른 사람(다른 계정)이 방에 글을 써야 합니다.
> 그 전에는 `ask "방" "질문"`으로 답을 확인합니다.

설정 항목은 `config.example.toml`에 설명이 있습니다.

## 윈도우

윈도우 카카오톡도 같은 방식(**UI Automation**)으로 읽을 수 있는지 아직 확인하지 못했습니다.
그래서 먼저 확인용 스크립트를 돌려야 합니다. 이 스크립트는 읽기만 합니다. 창을 앞으로 가져오거나 클릭·키 입력·클립보드를 쓰지 않습니다.

```powershell
git clone https://github.com/wonhp1/kakao-read.git
cd kakao-read
powershell -ExecutionPolicy ByPass -File scripts\setup.ps1     # uv·Claude Code 설치, 로그인 안내

# 방을 더블클릭해 별도 창으로 연다 (최소화하지 않기)
uv run probe/kakao_win_probe.py windows
uv run probe/kakao_win_probe.py probe "방 이름" --expect "화면에 보이는 메시지 일부"
```

맨 끝의 `[판정]`을 확인합니다.

| 판정 | 뜻 | 다음 단계 |
|---|---|---|
| 가능 | UIA에서 메시지 글이 나옵니다 | 이 경로로 `src/kakao_read/backends/windows.py`의 읽기·보내기를 채웁니다 |
| 부분 가능 | Win32 창 글자에서만 나옵니다 | 그 경로로 만들 수 있는지 봅니다 |
| 못 찾음 | 어디에도 나오지 않습니다 | NVDA(무료 스크린리더)를 켠 채로 카카오톡을 다시 켜고 확인합니다 |

- 카카오톡을 **관리자 권한으로 실행했다면** PowerShell도 관리자 권한으로 열어야 합니다.
- 자세한 결과는 `probe-report.json`에 남습니다.
  - 이 파일에는 **대화 일부가 들어 있습니다.** 긴 글은 앞 8자만 남기고, `--full`을 주면 전체를 남깁니다.
  - `.gitignore`에 들어 있습니다. 공개 저장소에 올리지 마세요.
- probe 옵션: `--hwnd 0x…`(같은 제목의 창이 여럿일 때), `--depth`·`--max-nodes`·`--max-seconds`(읽는 한도), `--out`, `--full`, `--print-lines`

## 구조

```
src/kakao_read/
  cli.py            명령 (doctor · login-help · windows · read · ask · run)
  bot.py            한 바퀴: 읽기 → 새 글 → 답할 글 → Claude → 보내기
  tracker.py        새로 올라온 글 가려내기 (번호가 없는 화면 글을 앞뒤 겹침으로 맞춘다)
  engine.py         Claude Code CLI 호출 (도구 차단), 설치·로그인 확인
  config.py         config.toml
  backends/
    base.py         플랫폼 공통 모양 (windows · read · send)
    mac.py          macOS — mac_ax.py 를 감싼다
    mac_ax.py       kakaotalk 스킬의 AX 읽기·보내기 사본
    windows.py      윈도우 — probe 결과를 보고 채울 자리
probe/kakao_win_probe.py   윈도우 UIA 확인용 (읽기 전용)
```

시험(가짜 카카오톡 창·가짜 엔진, 실제 전송·Claude 호출 없음):

```bash
uv run pytest -q
```

## 공개하지 않는 것

`config.toml`(방 이름·역할·자료 경로), 자료 폴더, `probe-report.json`, 로그는 `.gitignore`에 들어 있습니다.
비밀번호·로그인 코드는 설정 파일에 적지 않습니다. 로그인 정보는 Claude Code가 따로 보관합니다.
