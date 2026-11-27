$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$vendor = Join-Path $root "vendor"
$jdk = Join-Path $vendor "jdk"
if (-not (Test-Path $jdk)) {
    New-Item -ItemType Directory -Force $vendor | Out-Null
    New-Item -ItemType Junction -Path $jdk -Target $env:JAVA_HOME | Out-Null
}

& (Join-Path $PSScriptRoot "fetch-vendor.ps1")

# 스크립트 봇은 네이티브 추론을 쓰지 않는다. 고정한 main 소스에서 대국 호스트가 참조하는
# 클래스만 컴파일하고, 체크섬을 검증한 릴리스 jar를 외부 라이브러리 클래스패스로 쓴다.
$src = Join-Path $vendor "epsilon-src"
$releaseJar = Join-Path $vendor "epsilon-release/epsilon-prism-1.0-SNAPSHOT-all.jar"
$reviewerTarget = Join-Path $src "epsilon-reviewer/backend/target"
$classes = Join-Path $reviewerTarget "ci-classes"
$reviewerJar = Join-Path $reviewerTarget "epsilon-reviewer-1.0-SNAPSHOT-all.jar"
New-Item -ItemType Directory -Force $classes | Out-Null
$sourceRoots = Get-ChildItem $src -Recurse -Directory -Filter java |
    Where-Object { $_.FullName -match '[\\/]src[\\/]main[\\/]java$' } |
    Select-Object -ExpandProperty FullName
$sources = (Get-ChildItem (Join-Path $root "tools/live-match/src") -Recurse -Filter "*.java").FullName
& (Join-Path $jdk "bin/javac.exe") --release 21 -encoding UTF-8 -Xprefer:source `
    -cp $releaseJar -sourcepath ($sourceRoots -join ';') -d $classes $sources
Copy-Item $releaseJar $reviewerJar -Force
& (Join-Path $jdk "bin/jar.exe") --update --file $reviewerJar -C $classes com `
    -C (Join-Path $src "epsilon-mahjong/src/main/resources") shape
