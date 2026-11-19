# kakao-read 설치 (Windows PowerShell). 여러 번 돌려도 된다 — 있는 것은 건너뛰고, config.toml 은 덮어쓰지 않는다.
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1 -Mode command -NoLogin   # 묻지 않고 (AI 에이전트용)
#     -Mode command|auto  답장 방식 (command: 명령할 때만 · auto: 자동 답장). 주면 묻지 않고 config.toml 에 적는다
#     -NoLogin            Claude 로그인 창을 띄우지 않고 상태만 알려 준다 (로그인은 사람이 따로)
# Windows PowerShell 5.1 은 Stop 이면 외부 명령의 stderr 출력만으로 멈춘다 — 결과는 종료 코드로 직접 본다
param(
  [ValidateSet("", "command", "auto")] [string]$Mode = "",
  [switch]$NoLogin
)
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

function Bold($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Has($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function Set-ReplyMode($value) {
  $path = (Resolve-Path config.toml).Path
  $text = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
  if ($text -match '(?m)^mode = "(command|auto)"') {
    $text = $text -replace '(?m)^mode = "(command|auto)"', "mode = `"$value`""
  } else {
    $text = $text -replace '(?m)^\[reply\]\s*$', "[reply]`nmode = `"$value`""
  }
  [IO.File]::WriteAllText($path, $text, (New-Object Text.UTF8Encoding $false))   # BOM 없이 (TOML)
  if ($value -eq "auto") {
    Write-Host "답장 방식: 자동 답장. 처음엔 dry_run = true 라 미리보기만 하고, 실제 전송은 rooms.allowed_send 의 방만 합니다."
  } else {
    Write-Host "답장 방식: 명령할 때만 (kakao-read reply)."
  }
}
$localBin = Join-Path $env:USERPROFILE ".local\bin"
if ($env:Path -notlike "*$localBin*") { $env:Path = "$localBin;$env:Path" }

Bold "1/5 uv (파이썬 실행 도구)"
if (Has "uv") {
  Write-Host "있음: $(uv --version)"
} else {
  Write-Host "없어서 설치합니다 (https://astral.sh/uv)"
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  if (-not (Has "uv")) { Write-Host "uv 설치를 확인하지 못했습니다 — 새 PowerShell 창에서 다시 실행하세요"; exit 1 }
}

Bold "2/5 Claude Code (답을 만드는 엔진)"
if (Has "claude") {
  Write-Host "있음: $(claude --version)"
} else {
  Write-Host "없어서 설치합니다 (공식 설치 스크립트 https://claude.ai/install.ps1 — 1~2분 걸릴 수 있습니다)"
  Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
  if (-not (Has "claude")) { Write-Host "claude 설치를 확인하지 못했습니다 — 새 PowerShell 창에서 다시 실행하세요"; exit 1 }
  Write-Host "설치됨: $(claude --version)"
}

Bold "3/5 Claude Code 로그인"
claude auth status *> $null
if ($LASTEXITCODE -eq 0) {
  Write-Host "로그인 돼 있습니다"
} elseif ($NoLogin) {
  Write-Host "아직 로그인 전입니다 — PowerShell 에서 'claude auth login' 을 실행하고 브라우저에서 승인하세요." -ForegroundColor Yellow
} else {
  Write-Host @"
아직 로그인하지 않았습니다. 지금 로그인 창을 띄웁니다.

  · 브라우저가 열리면 claude.ai 에서 쓰는 계정으로 로그인하고 '승인(Authorize)'을 누르세요.
  · Claude Pro·Max 구독이면 그대로 진행합니다. 구독 없이 API 요금으로 쓰려면
    Ctrl+C 로 멈추고 'claude auth login --console' 을 직접 입력하세요.
  · 브라우저가 안 열리면 아래에 나오는 주소를 복사해 브라우저에 붙여 넣으세요.
  · 브라우저가 코드를 보여 주면 복사해서 이 창에 붙여 넣고 Enter.
"@
  $answer = Read-Host "Enter 를 누르면 로그인을 시작합니다 (건너뛰려면 n)"
  if ($answer -ne "n" -and $answer -ne "N") { claude auth login }
  claude auth status *> $null
  if ($LASTEXITCODE -eq 0) { Write-Host "로그인 됐습니다" -ForegroundColor Green }
  else {
    Write-Host "아직 로그인 전입니다. 나중에 'claude auth login' 을 실행하세요." -ForegroundColor Yellow
    Write-Host "자세한 방법: uv run kakao-read login-help"
  }
}

Bold "4/5 파이썬 패키지"
uv sync --quiet
if ($LASTEXITCODE -ne 0) {
  # 일부 PC(예: ARM64)에서 uv 가 최신 Python 설치 중 'Missing expected target directory' 로 실패한다 — 3.13 으로
  Write-Host "기본 Python 으로 실패해 Python 3.13 으로 다시 합니다"
  uv python install 3.13
  uv sync --quiet --python 3.13
  if ($LASTEXITCODE -ne 0) { Write-Host "파이썬 패키지 설치에 실패했습니다 — 위 오류를 확인하세요"; exit 1 }
}
Write-Host "완료"

Bold "5/5 설정 파일"
$created = $false
if (Test-Path config.toml) { Write-Host "config.toml 이 이미 있어 그대로 둡니다" }
else { Copy-Item config.example.toml config.toml; $created = $true; Write-Host "config.toml 을 만들었습니다" }
if ($Mode) {
  Set-ReplyMode $Mode
} elseif ($created) {
  Write-Host @"

답장 방식을 고르세요 (나중에 config.toml 의 [reply] mode 로 바꿀 수 있습니다)
  1) 명령할 때만 (기본) — kakao-read reply "방" 을 실행할 때만, 답을 보여 주고 확인받은 뒤 보냅니다
  2) 자동 답장          — kakao-read run 을 켜 두면 새 메시지에 알아서 답합니다
"@
  $choice = Read-Host "번호 (Enter = 1)"
  if ($choice -eq "2") { Set-ReplyMode "auto" } else { Set-ReplyMode "command" }
}

Write-Host @"

다음 단계:
  1. 카카오톡에서 방을 더블클릭해 별도 창으로 엽니다 (최소화하지 않기).
  2. uv run kakao-read windows                 # 열린 방 창 제목
  3. uv run kakao-read read "방 이름" -n 20     # 최근 메시지
  4. uv run kakao-read reply "방 이름"          # (명령 모드) 지금 답장 쓰기 → 확인 후 보내기
     uv run kakao-read run                     # (자동 모드) 새 메시지에 알아서 답하기
  5. uv run kakao-read doctor                  # 점검
"@
