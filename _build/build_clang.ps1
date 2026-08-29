# Build the generated project with psxrecomp's own clang/MinGW toolchain.
# This is the toolchain the framework actually targets (x86_64-w64-windows-gnu);
# the MSVC path hit two MSVC-only defects, so this is the supported route.
param(
  [string]$Project  = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp",
  [string]$BuildDir = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp\build-clang",
  [switch]$Reconfigure
)
$ErrorActionPreference = 'Stop'
$T = "c:\Users\yhgoz\.local\share\retcomm\toolchains\cmake-clang-v1\1.0.14"
if (-not (Test-Path "$T\bin\clang.exe")) { throw "clang toolchain not found at $T" }

# Toolchain bin goes first so its clang/ninja/cmake win over anything else.
$env:PATH = "$T\bin;$env:PATH"
$env:CC  = "$T\bin\clang.exe"
$env:CXX = "$T\bin\clang++.exe"

Write-Host ("clang : " + (& "$T\bin\clang.exe" --version | Select-Object -First 1))
Write-Host ("cmake : " + (& "$T\bin\cmake.exe" --version | Select-Object -First 1))
Write-Host ("ninja : " + (& "$T\bin\ninja.exe" --version))

if ($Reconfigure -and (Test-Path $BuildDir)) {
  Write-Host "Clearing $BuildDir for a clean configure..."
  Remove-Item -Recurse -Force $BuildDir
}

Write-Host "`n=== CONFIGURE (clang / mingw) ===`n"
& "$T\bin\cmake.exe" -S $Project -B $BuildDir -G Ninja `
  -DCMAKE_BUILD_TYPE=Release `
  -DCMAKE_C_COMPILER="$T/bin/clang.exe" `
  -DCMAKE_CXX_COMPILER="$T/bin/clang++.exe" `
  -DCMAKE_MAKE_PROGRAM="$T/bin/ninja.exe" `
  -DPSX_RECOMP_UI=OFF
if ($LASTEXITCODE -ne 0) { Write-Host "CONFIGURE FAILED ($LASTEXITCODE)"; exit $LASTEXITCODE }

Write-Host "`n=== BUILD ===`n"
& "$T\bin\cmake.exe" --build $BuildDir --parallel
$rc = $LASTEXITCODE
Write-Host "`n=== BUILD EXIT: $rc ==="
if ($rc -eq 0) {
  Get-ChildItem $BuildDir -Filter "*.exe" -EA SilentlyContinue |
    ForEach-Object { "ARTIFACT: {0}  ({1:N1} MB)" -f $_.Name, ($_.Length/1MB) }
}
exit $rc
