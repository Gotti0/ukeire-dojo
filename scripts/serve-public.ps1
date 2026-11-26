# 트레이너를 외부(DDNS)에 공개한다. 빌드한 화면과 API를 Python 서버 하나가 0.0.0.0:$Port에서 서빙하고, Basic 인증 암호를 건다.
# 공유기에서 이 PC의 $Port(TCP)로 포트 포워딩하고, Windows 방화벽에서 인바운드를 허용하는 것은 직접 해야 한다.
param(
  [int]$Port = 5173,
  [string]$Db = "data\bank.sqlite"
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$passwordFile = Join-Path $root "data\server-password.txt"
if (-not (Test-Path $passwordFile)) {
  # 암호는 data/(커밋 제외)에만 둔다. 화면에 출력하지 않으니 파일을 열어 지인에게 따로 전한다.
  python -c "import secrets; print(secrets.token_urlsafe(18))" | Set-Content -NoNewline -Encoding ascii $passwordFile
  "created password file: $passwordFile"
}
if (-not (Test-Path (Join-Path $root "web\dist\index.html"))) {
  Push-Location (Join-Path $root "web"); npm install --no-fund --no-audit; npm run build; Pop-Location
}
# 서버는 접속 기록을 stderr(이 창)와 로그 파일에 직접 쓴다. stderr를 파이프로 받지 않으므로 Windows PowerShell 5.1의
# "Stop일 때 stderr를 종료 오류로 바꾸는" 동작에도 걸리지 않는다.
$ErrorActionPreference = "Continue"
python -m ukeire_dojo.server --host 0.0.0.0 --port $Port --db $Db --password-file $passwordFile `
  --log-file (Join-Path $root "data\server-access.log")
# 서버가 오류로 끝났는지 serve-public.bat이 알 수 있게 종료 코드를 넘긴다.
exit $LASTEXITCODE
