# 시스템 JDK 23은 bin에 VC++ 런타임 14.36을 묶어 두는데, libtorch 2.11의 c10.dll은 이 버전으로 초기화되지 않는다
# (UnsatisfiedLinkError: DLL 초기화 루틴 실패). 시스템 JDK를 건드리지 않으려고 사본을 만들고 런타임 DLL만 시스템 것으로 바꾼다.
param(
  [string]$SourceJdk = "C:\Program Files\Java\jdk-23",
  [string]$Target = (Join-Path $PSScriptRoot "..\vendor\jdk")
)
$ErrorActionPreference = "Stop"

if (Test-Path $Target) { Remove-Item -Recurse -Force $Target -Confirm:$false }
Copy-Item -Recurse $SourceJdk $Target
foreach ($dll in "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll") {
  Copy-Item (Join-Path $env:SystemRoot "System32\$dll") (Join-Path $Target "bin\$dll") -Force
}
Get-ChildItem (Join-Path $Target "bin") -Filter "*140*.dll" |
  ForEach-Object { "{0}  {1}" -f $_.Name, $_.VersionInfo.FileVersion }
