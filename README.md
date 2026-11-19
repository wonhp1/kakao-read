# kakao-read

카카오톡 PC의 **방 창을 읽고, Claude가 답장을 써서 보내는** 도구입니다.

- 화면 접근성 기능으로 열린 방 창의 글을 읽습니다. 서버 접속·로그인 가로채기·DB 복호화는 하지 않습니다.
- 답은 내 컴퓨터에 설치한 **Claude Code**(`claude`)가 만듭니다. API 키가 없어도 됩니다.
- 처음에는 **미리보기(dry_run)** 로만 돕니다. 실제 전송은 사용자가 고른 방에서만 켜집니다.

| 플랫폼 | 읽기 | AI 답장 보내기 | 상태 |
|---|---|---|---|
| macOS | ✅ 접근성(AX) | ✅ | 읽기·보내기는 카카오톡 26.8에서 실측했습니다. 봇 루프는 가짜 창으로만 시험했습니다 |
| Windows | ✅ 클립보드 복사 | ✅ | 읽기·보내기는 카카오톡 26.10(Windows 11)에서 실측했습니다. 창을 앞으로 가져오지 않습니다 ([아래](#윈도우)) |

## 답장 방식 두 가지

설치할 때 고르고, 나중에 `config.toml`의 `[reply] mode`로 바꿀 수 있습니다.

| `mode` | 언제 답하나 | 명령 |
|---|---|---|
| `"command"` (기본) | **내가 명령할 때만.** 그 방의 최근 대화를 읽고 내 말투로 답을 써서, 보여 준 뒤 확인을 받고 보냅니다 | `kakao-read reply "방"` · `kakao-read reply "방" "생일 축하한다고 해줘"` (`--yes` 바로 보냄, `--dry` 보기만) |
| `"auto"` | **새 메시지가 오면 알아서.** 아래 '동작'대로 돕니다 | `kakao-read run` |

두 방식 모두 처음 보는 방이면 먼저 지난 대화를 읽어 프로필(맥락·말투)을 만듭니다.

## 동작 (자동 답장)

```
몇 초마다 방 창 읽기 → 새로 올라온 글 가려내기 → 답할 글 고르기 → Claude에게 묻기 → 보내기(또는 미리보기)
```

- **답하는 글**: 다른 사람이 보낸 글만 답합니다. 내 계정이 보낸 글(봇의 답 포함), 입퇴장 공지,
  '채팅방에 함께 보내기'로 올라온 스레드 댓글에는 답하지 않습니다.
  한 사람이 이어서 보낸 글은 한 질문으로 묶습니다.
- **답하는 조건**(`reply.trigger`)
  - `keyword`(기본): `!ai`처럼 정한 낱말이 들어간 글에만 답합니다.
  - `all`: 받은 글을 모두 Claude에게 보여 주고, 답할 필요가 있을 때만 답합니다.
- **처음 보는 방은 지난 대화를 모두 읽고 시작합니다** (`ai.learn_history`, 기본 켜짐).
  관계·호칭, 요즘 이야기, **나의 말투**(반말/존댓말·문장 길이·ㅋㅋ 습관·자주 쓰는 표현)를 Claude가 정리해
  `profiles/<방>.md`에 저장하고, 답할 때 그 말투를 따릅니다. 내 실제 메시지 예시도 함께 남깁니다.
  - 방 창을 맨 위까지 올려 과거 메시지를 불러온 뒤 다 읽고 맨 아래로 되돌립니다. 포커스·마우스는 그대로입니다.
    - Windows: 휠 메시지로 올리고 클립보드로 읽습니다 (메시지 400여 개에 10~50초).
    - macOS: 스크롤바를 맨 위로 맞추면 카카오톡이 약 50줄씩 더 불러옵니다. 다 불러오면 위에서부터 한 화면씩 내리며
      보이는 줄만 읽습니다 (화면 밖 줄은 접근성으로 아주 느려서). 메시지 1300개쯤에 1분 안팎(26.9 실측),
      Claude 정리까지 2분쯤. 그동안 방 창이 화면에 보여야 합니다 (최소화·숨김이면 꺼내 달라고 알립니다).
  - 그다음부터는 저장한 프로필을 쓰고 **새로 올라온 메시지만** 봅니다. 다시 만들려면 `kakao-read learn "방"`.
  - 프로필에는 대화 요약이 들어 있습니다. `.gitignore`에 있으니 공개 저장소에 올리지 마세요.
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
   - 점검 항목: Claude Code 설치·로그인, macOS 버전, **카카오톡 설치·실행·버전**(실측한 26.8·26.9와 다르면 ⚠️),
     **손쉬운 사용 권한**(켜야 할 앱 이름까지), 열린 방 창.
   - 안 된 것이 있으면 끝에 **'다음 할 일'** 로 고치는 법을 차례로 보여 줍니다. 하고 나서 `uv run kakao-read doctor`로 다시 점검합니다.

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
uv run kakao-read doctor                      # 점검: 엔진·로그인·카카오톡 버전·권한·방 창 → 다음 할 일
uv run kakao-read read "방 이름" -n 20        # 최근 메시지 보기
uv run kakao-read send "방 이름" "글" --yes    # 메시지 하나 직접 보내기 (--yes 없으면 미리보기)
uv run kakao-read reply "방 이름" ["요청"]     # (명령 모드) 지금 답장 쓰기 → 확인 후 보내기
uv run kakao-read learn "방 이름"              # 지난 대화 전부 → 방 프로필(맥락·말투) 새로 만들기
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

```powershell
git clone https://github.com/wonhp1/kakao-read.git
cd kakao-read
powershell -ExecutionPolicy ByPass -File scripts\setup.ps1     # uv·Claude Code 설치, 로그인 안내

# 방을 더블클릭해 별도 창으로 연다 (최소화하지 않기)
uv run kakao-read windows
uv run kakao-read read "방 이름" -n 20
uv run kakao-read send "방 이름" "글" --yes
```

윈도우 카카오톡(26.10)은 UI Automation에 메시지 목록을 내주지 않습니다 (probe 결과: 입력창과 빈 목록 컨트롤뿐).
그래서 Win32 창 메시지로 읽고 보냅니다. **창을 앞으로 가져오거나 포커스를 바꾸지 않습니다.**

- **읽기**: 방 창의 메시지 목록에 Ctrl+A·Ctrl+C 키 메시지를 보내 대화를 클립보드로 받고,
  `[보낸 사람] [오후 3:45] 내용` 줄을 나눕니다.
  - 원래 클립보드의 **글**은 읽은 뒤 되돌려 놓습니다. 그림·파일이 들어 있었으면 되돌리지 못합니다.
  - 읽은 뒤 방 창에서 대화 전체가 선택된 것처럼(회색) 보일 수 있습니다.
  - 내가 보낸 글 구분: 1:1 방은 방 제목이 아닌 쪽, 단톡방은 `KAKAO_MY_NAME` 환경 변수(카톡 이름)로 정합니다.
    이 프로그램이 한 번 보내고 나면 그 이름을 기억합니다.
- **보내기**: 카카오톡은 WM_SETTEXT로 넣은 글을 '입력된 글'로 치지 않아 Enter를 눌러도 보내지 않습니다
  (전송 버튼도 꺼진 채). 그래서 ① WM_CHAR로 한 자씩 치고 ② 입력창에 Enter를 바로 보내 입력을 확정시킨 뒤
  ③ WM_SETTEXT로 정확한 글로 맞추고 ④ Enter를 PostMessage로 보냅니다.
  - 보낸 뒤 대화를 다시 읽어 **새 말풍선을 확인**합니다. 확인 못 하면 **최대 3번** 다시 시도합니다.
  - 다시 시도하는 것은 입력창에 글이 그대로 남아 **안 나간 게 확실할 때뿐**입니다.
    글이 사라졌는데 말풍선이 안 보이면 나갔을 수 있으니 다시 보내지 않습니다 (중복 방지).
  - 입력창에 사람이 쓰던 글이 있으면 덮어쓰지 않습니다.
- 카카오톡을 **관리자 권한으로 실행했다면** PowerShell도 관리자 권한으로 열어야 합니다.

### probe (진단용)

다른 카카오톡 버전에서 창 구조가 다를 때 확인하는 스크립트입니다. 읽기만 합니다.

```powershell
uv run probe/kakao_win_probe.py windows
uv run probe/kakao_win_probe.py probe "방 이름" --expect "화면에 보이는 메시지 일부"
```

- 자세한 결과는 `probe-report.json`에 남습니다.
  - 이 파일에는 **대화 일부가 들어 있습니다.** 긴 글은 앞 8자만 남기고, `--full`을 주면 전체를 남깁니다.
  - `.gitignore`에 들어 있습니다. 공개 저장소에 올리지 마세요.
- probe 옵션: `--hwnd 0x…`(같은 제목의 창이 여럿일 때), `--depth`·`--max-nodes`·`--max-seconds`(읽는 한도), `--out`, `--full`, `--print-lines`

## 구조

```
src/kakao_read/
  cli.py            명령 (doctor · login-help · windows · read · send · reply · learn · ask · run)
  bot.py            한 바퀴: 읽기 → 새 글 → 답할 글 → Claude → 보내기
  profile.py        처음 보는 방: 지난 대화 전부 → 맥락·나의 말투 정리 (profiles/<방>.md)
  tracker.py        새로 올라온 글 가려내기 (번호가 없는 화면 글을 앞뒤 겹침으로 맞춘다)
  engine.py         Claude Code CLI 호출 (도구 차단), 설치·로그인 확인
  config.py         config.toml
  backends/
    base.py         플랫폼 공통 모양 (windows · read · send)
    mac.py          macOS — mac_ax.py 를 감싼다
    mac_ax.py       kakaotalk 스킬의 AX 읽기·보내기·지난 대화 전부 읽기(read_history) 사본
    windows.py      윈도우 — Win32 메시지로 읽기(클립보드)·보내기(WM_CHAR + Enter), 확인·재시도 3번
probe/kakao_win_probe.py   윈도우 UIA 확인용 (읽기 전용)
```

시험(가짜 카카오톡 창·가짜 엔진, 실제 전송·Claude 호출 없음):

```bash
uv run pytest -q
```

## 공개하지 않는 것

`config.toml`(방 이름·역할·자료 경로), 자료 폴더, `probe-report.json`, 로그는 `.gitignore`에 들어 있습니다.
비밀번호·로그인 코드는 설정 파일에 적지 않습니다. 로그인 정보는 Claude Code가 따로 보관합니다.
