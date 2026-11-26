# Epsilon main을 NVIDIA용(CUDA JNI + PRISM CUDA 커널)으로 빌드한다.
# 릴리스 v760 jar는 GPU 어텐션이 AMD(ROCm) 빌드에만 있어 NVIDIA에서 돌지 않으므로, main의 자체 CUDA 추론 커널을 쓴다.
param(
  [string]$Arch = "89",  # RTX 4070 Ti = sm_89. 저장소 기본값은 sm_120이다.
  [string]$Modules = "epsilon-prism-4p,epsilon-reviewer/backend"
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$src = Join-Path $root "vendor\epsilon-src"

# nvcc와 CMake가 MSVC 환경 변수를 요구하므로 x64 개발자 환경을 현재 프로세스에 들여온다.
$vs = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools"
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
cmd /c "`"$vcvars`" >nul && set" | ForEach-Object {
  if ($_ -match "^([^=]+)=(.*)$") { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) }
}
$cmakeTools = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake"
$env:PATH = (Join-Path $cmakeTools "CMake\bin") + ";" + (Join-Path $cmakeTools "Ninja") + ";" + $env:PATH
# CMake의 기본 VS 생성기는 CUDA의 VS 통합(MSBuild 확장)을 요구하는데 Build Tools 2022에는 없다("No CUDA toolset found").
# Ninja 생성기는 nvcc를 직접 부르므로 통합 없이 된다.
$env:CMAKE_GENERATOR = "Ninja"
$env:CUDACXX = Join-Path $env:CUDA_PATH "bin\nvcc.exe"
$env:JAVA_HOME = Join-Path $root "vendor\jdk"
# 이전 실패가 남긴 CMake 캐시에는 VS 생성기가 기록돼 있어 생성기를 바꾸면 거부되므로 지운다.
Remove-Item -Recurse -Force (Join-Path $src "epsilon-prism-kernel\cuda\target") -ErrorAction SilentlyContinue

# 토큰은 화면이나 파일에 남기지 않고 이 프로세스의 환경 변수로만 둔다.
$env:GITHUB_ACTOR = (gh api user --jq .login)
$env:GITHUB_TOKEN = (gh auth token)
try {
  Push-Location $src
  $log = Join-Path $root "data\build-epsilon-cuda.log"
  & .\mvnw.cmd -B -s (Join-Path $PSScriptRoot "maven-settings.xml") `
    "-Pcuda,native-cuda" "-Dprism.cuda.arch=$Arch" -pl $Modules -am -DskipTests package *> $log
  if ($LASTEXITCODE -ne 0) { throw "maven failed with exit code $LASTEXITCODE; see $log" }
} finally {
  Pop-Location
  Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
}
Get-ChildItem (Join-Path $src "epsilon-prism-4p\target\*-all.jar"), (Join-Path $src "epsilon-reviewer\backend\target\*-all.jar") |
  Select-Object Name, Length
