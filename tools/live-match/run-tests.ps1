$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$jdk = Join-Path $root "vendor/jdk/bin"
$jar = Join-Path $root "vendor/epsilon-src/epsilon-reviewer/backend/target/epsilon-reviewer-1.0-SNAPSHOT-all.jar"
$classes = Join-Path $root "data/live-match/classes"
New-Item -ItemType Directory -Force -Path $classes | Out-Null
$files = (Get-ChildItem (Join-Path $PSScriptRoot "src"), (Join-Path $PSScriptRoot "test") -Recurse -Filter "*.java").FullName

& "$jdk/javac.exe" --release 21 -encoding UTF-8 -cp $jar -d $classes $files
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$jdk/java.exe" -cp "$classes;$jar" ukeire_dojo.live.EastMatchTest
exit $LASTEXITCODE
