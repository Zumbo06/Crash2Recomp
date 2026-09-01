$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeCMake = Join-Path $Root 'psxrecomp/runtime/runtime.cmake'
if (-not (Test-Path -LiteralPath $RuntimeCMake)) {
  Write-Error "PSXRecomp runtime is missing: $RuntimeCMake`nThis generated project needs the psxrecomp framework tree at '$Root\psxrecomp'.`nIf this project came from git, run: git submodule update --init --recursive`nIf this project came from psxrecomp.exe, regenerate it from the full CLI zip and keep the generated psxrecomp folder."
  exit 1
}
cmake -S $Root -B (Join-Path $Root 'build') -G Ninja -DCMAKE_BUILD_TYPE=Release -DPSX_RECOMP_UI=OFF
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
cmake --build (Join-Path $Root 'build') --config Release --parallel
exit $LASTEXITCODE
