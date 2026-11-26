param(
    [ValidateSet("cpu", "gpu0")]
    [string] $Device = "gpu0"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$jdk = Join-Path $root "vendor/jdk/bin"
$jar = Join-Path $root "vendor/epsilon-src/epsilon-reviewer/backend/target/epsilon-reviewer-1.0-SNAPSHOT-all.jar"
$release = Join-Path $root "vendor/epsilon-release/epsilon-prism-1.0-SNAPSHOT-all.jar"
$checkpoint = Join-Path $root "vendor/epsilon-release/epsilon-prism-v760"
$run = Join-Path $root "data/live-spike"
$classes = Join-Path $run "classes"
New-Item -ItemType Directory -Force -Path $classes | Out-Null

& "$jdk/javac.exe" --release 21 -encoding UTF-8 -cp $jar -d $classes (Join-Path $PSScriptRoot "src/ukeire_dojo/live/LiveSpike.java")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$originalFlavor = $env:PYTORCH_FLAVOR
$originalPath = $env:PATH
Push-Location $run
try {
    if ($Device -eq "cpu") {
        # CUDA 빌드에는 CPU JNI 리소스가 없다. 기존 릴리스에서 DLL 리소스만 클래스패스에 보탠다.
        Push-Location $classes
        try {
            & "$jdk/jar.exe" xf $release "jnilib/win-x86_64/cpu/djl_torch.dll"
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        } finally { Pop-Location }
        $env:PYTORCH_FLAVOR = "cpu"
    } else {
        $env:PYTORCH_FLAVOR = "cu128"
        $env:PATH = "$HOME/.djl.ai/pytorch/2.11.0-cu128-win-x86_64;$originalPath"
    }
    & "$jdk/java.exe" "-Djdk.net.unixdomain.tmpdir=$run" -cp "$classes;$jar" ukeire_dojo.live.LiveSpike $checkpoint $Device
    exit $LASTEXITCODE
} finally {
    Pop-Location
    $env:PYTORCH_FLAVOR = $originalFlavor
    $env:PATH = $originalPath
}
