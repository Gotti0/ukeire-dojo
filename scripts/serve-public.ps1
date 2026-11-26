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
python -m ukeire_dojo.server --host 0.0.0.0 --port $Port --db $Db --password-file $passwordFile 2>&1 |
  Tee-Object -FilePath (Join-Path $root "data\server-access.log") -Append
