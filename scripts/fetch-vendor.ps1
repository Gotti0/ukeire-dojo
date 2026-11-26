# vendor/를 다시 만든다. vendor/는 크고 재배포 조건이 서로 달라 저장소에 넣지 않는다.
# 받는 것: Epsilon 소스(main을 검증한 커밋으로 고정), Epsilon-Prism v760 릴리스의 모델 zip과 jar(체크섬 확인).
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$vendor = Join-Path $root "vendor"
New-Item -ItemType Directory -Force $vendor | Out-Null

# 2026-11-26에 CUDA 빌드와 v760 가중치 호환을 확인한 main 커밋.
$epsilonCommit = "7630d15bc70a1fb9557408e2e7cd12e0b20a8d7b"
$src = Join-Path $vendor "epsilon-src"
if (-not (Test-Path $src)) { git clone https://github.com/KoutaChan/Epsilon.git $src }
git -C $src fetch --tags
git -C $src checkout --quiet $epsilonCommit

$release = Join-Path $vendor "epsilon-release"
New-Item -ItemType Directory -Force $release | Out-Null
$base = "https://github.com/KoutaChan/Epsilon/releases/download/epsilon-prism-v760"
$expected = @{
  "epsilon-prism-v760.zip"             = "d1c269fbbd4153ec4840fe3950cbd62fd14874bbdd263ebd377c7ae38cc48239"
  "epsilon-prism-1.0-SNAPSHOT-all.jar" = "d2e67af6946631890db3b088feb48cc5bf85f0ca8d29c1f680cdb6986893a34b"
}
foreach ($name in $expected.Keys) {
  $file = Join-Path $release $name
  if (-not (Test-Path $file)) { Invoke-WebRequest "$base/$name" -OutFile $file }
  $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
  if ($hash -ne $expected[$name]) { throw "checksum mismatch for $name : $hash" }
}
Expand-Archive -Force (Join-Path $release "epsilon-prism-v760.zip") $release

# v760 태그의 리뷰어 백엔드를 릴리스 jar 위에서 컴파일한다(CPU 엔진). 이유는 spike/T1-epsilon-spike.md.
$reviewer = Join-Path $vendor "reviewer-v760"
if (-not (Test-Path (Join-Path $reviewer "classes"))) {
  New-Item -ItemType Directory -Force $reviewer | Out-Null
  git -C $src archive epsilon-prism-v760 epsilon-reviewer/backend/src/main | tar -x -C $reviewer
  $sources = Get-ChildItem -Recurse (Join-Path $reviewer "epsilon-reviewer") -Filter *.java | ForEach-Object FullName
  $list = Join-Path $reviewer "sources.txt"
  $sources | Set-Content $list -Encoding ascii
  & (Join-Path $vendor "jdk\bin\javac.exe") --release 21 -nowarn -encoding UTF-8 `
    -cp (Join-Path $release "epsilon-prism-1.0-SNAPSHOT-all.jar") -d (Join-Path $reviewer "classes") "@$list"
  if ($LASTEXITCODE -ne 0) { throw "reviewer-v760 compile failed" }
}
"vendor ready: $vendor"
