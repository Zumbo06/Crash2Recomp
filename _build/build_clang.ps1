# Build the generated project with psxrecomp's own clang/MinGW toolchain.
# This is the toolchain the framework actually targets (x86_64-w64-windows-gnu);
# the MSVC path hit two MSVC-only defects, so this is the supported route.
#
# BUILDS BOTH TREES BY DEFAULT:
#   build-clang       PSX_DEBUG_TOOLS=OFF - what the launcher runs, ships to dist
#   build-debugtools  PSX_DEBUG_TOOLS=ON  - TCP debug server for spu_events /
#                                           gpu_state / ws_census diagnostics
#
# They were previously separate scripts, and the diagnostics tree silently went
# ~23 hours stale while runtime patches landed only in build-clang - so captures
# were being taken against a binary that predated the code under investigation.
# Keeping both in one script is what stops that recurring.
param(
  [string]$Project = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp",
  # release | debugtools | both
  [ValidateSet('release', 'debugtools', 'both')]
  [string]$Only = 'both',
  [switch]$Reconfigure
)
$ErrorActionPreference = 'Stop'

# Resolve the toolchain by version rather than pinning one, so a toolchain
# update does not silently break the build.
$packRoot = "c:\Users\yhgoz\.local\share\retcomm\toolchains\cmake-clang-v1"
$T = $null
foreach ($cand in @("$packRoot\latest") + (
        Get-ChildItem $packRoot -Directory -EA SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { $_.FullName })) {
  if (Test-Path "$cand\bin\clang.exe") { $T = $cand; break }
}
if (-not $T) { throw "clang toolchain not found under $packRoot" }

# Toolchain bin goes first so its clang/ninja/cmake win over anything else.
$env:PATH = "$T\bin;$env:PATH"
$env:CC = "$T\bin\clang.exe"
$env:CXX = "$T\bin\clang++.exe"

Write-Host ("toolchain : " + $T)
Write-Host ("clang     : " + (& "$T\bin\clang.exe" --version | Select-Object -First 1))
Write-Host ("cmake     : " + (& "$T\bin\cmake.exe" --version | Select-Object -First 1))

function Build-Tree {
  param([string]$Dir, [string]$DebugTools, [string]$Label)

  if ($Reconfigure -and (Test-Path $Dir)) {
    Write-Host "Clearing $Dir for a clean configure..."
    Remove-Item -Recurse -Force $Dir
  }

  Write-Host "`n=== CONFIGURE [$Label]  (PSX_DEBUG_TOOLS=$DebugTools) ===`n"
  & "$T\bin\cmake.exe" -S $Project -B $Dir -G Ninja `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_C_COMPILER="$T/bin/clang.exe" `
    -DCMAKE_CXX_COMPILER="$T/bin/clang++.exe" `
    -DCMAKE_MAKE_PROGRAM="$T/bin/ninja.exe" `
    -DPSX_RECOMP_UI=OFF `
    -DPSX_DEBUG_TOOLS=$DebugTools
  if ($LASTEXITCODE -ne 0) {
    Write-Host "CONFIGURE FAILED [$Label] ($LASTEXITCODE)"
    return $LASTEXITCODE
  }

  Write-Host "`n=== BUILD [$Label] ===`n"
  & "$T\bin\cmake.exe" --build $Dir --parallel
  $rc = $LASTEXITCODE
  if ($rc -eq 0) {
    Get-ChildItem $Dir -Filter "*.exe" -EA SilentlyContinue | ForEach-Object {
      "ARTIFACT [{0}]: {1}  ({2:N1} MB)  {3}" -f `
        $Label, $_.Name, ($_.Length / 1MB), $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    }
  }
  return $rc
}

$failed = 0
if ($Only -in @('release', 'both')) {
  $rc = Build-Tree "$Project\build-clang" 'OFF' 'release'
  if ($rc -ne 0) { $failed = $rc }
}
if ($Only -in @('debugtools', 'both')) {
  $rc = Build-Tree "$Project\build-debugtools" 'ON' 'debugtools'
  if ($rc -ne 0) { $failed = $rc }
}

# Surface drift immediately: if the two binaries are far apart in time, one of
# them is stale and any diagnosis taken from it is suspect.
Write-Host "`n=== BUILD SUMMARY ==="
$stamps = @{}
foreach ($d in @('build-clang', 'build-debugtools')) {
  $exe = Join-Path "$Project\$d" 'Crash_Bandicoot_2_Recompiled.exe'
  if (Test-Path $exe) {
    $t = (Get-Item $exe).LastWriteTime
    $stamps[$d] = $t
    Write-Host ("  {0,-17} {1}" -f $d, $t.ToString('yyyy-MM-dd HH:mm:ss'))
  } else {
    Write-Host ("  {0,-17} (not built)" -f $d)
  }
}
if ($stamps.Count -eq 2) {
  $skew = [math]::Abs(($stamps['build-clang'] - $stamps['build-debugtools']).TotalMinutes)
  if ($skew -gt 10) {
    Write-Host ("  WARNING: trees differ by {0:N0} min - one is stale" -f $skew)
  } else {
    Write-Host "  both trees in sync"
  }
}
Write-Host "`n=== BUILD EXIT: $failed ==="
exit $failed
