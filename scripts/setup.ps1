# kakao-read 설치 (Windows PowerShell). 여러 번 돌려도 된다 — 있는 것은 건너뛰고, config.toml 은 덮어쓰지 않는다.
#   powershell -ExecutionPolicy ByPass -File scripts\setup.ps1
# 윈도우 카카오톡 읽기·보내기는 아직 준비 중이다. 이 스크립트는 엔진(Claude Code)과 확인용 스크립트를 준비한다.
# Windows PowerShell 5.1 은 Stop 이면 외부 명령의 stderr 출력만으로 멈춘다 — 결과는 종료 코드로 직접 본다
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

function Bold($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
function Has($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
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
Write-Host "완료"

Bold "5/5 설정 파일"
if (Test-Path config.toml) { Write-Host "config.toml 이 이미 있어 그대로 둡니다" }
else { Copy-Item config.example.toml config.toml; Write-Host "config.toml 을 만들었습니다" }

Write-Host @"

윈도우 카카오톡 읽기·보내기는 아직 준비 중입니다. 먼저 읽을 수 있는지 확인해 주세요.
  1. 카카오톡에서 방을 더블클릭해 별도 창으로 엽니다 (최소화하지 않기).
  2. uv run probe/kakao_win_probe.py windows
  3. uv run probe/kakao_win_probe.py probe "방 이름" --expect "화면에 보이는 메시지 일부"
  4. 맨 끝 [판정] 과 probe-report.json 을 개발자에게 전달합니다 (대화 일부가 들어 있으니 공개 저장소에는 올리지 마세요).
엔진은 지금도 시험할 수 있습니다:  uv run kakao-read login-help
"@
