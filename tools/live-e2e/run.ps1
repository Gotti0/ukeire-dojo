param(
    [string] $Device = "gpu0"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path

if (-not $env:PLAYWRIGHT_ROOT) {
    $found = Get-ChildItem "$env:LOCALAPPDATA\npm-cache\_npx" -Recurse -Filter "playwright" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        $env:PLAYWRIGHT_ROOT = $found.FullName
    }
}
if (-not $env:PLAYWRIGHT_ROOT -or -not (Test-Path $env:PLAYWRIGHT_ROOT)) {
    throw "Playwright module not found. Set PLAYWRIGHT_ROOT to the playwright package directory."
}

Push-Location $root
try {
    & node (Join-Path $PSScriptRoot "verify.mjs") --device $Device
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
