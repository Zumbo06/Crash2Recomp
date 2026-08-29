# Build the generated Crash2Recomp project.
# Lives in _build/ (disposable). Wires up MSVC + our local ninja, then configures and builds.
param(
  [string]$Project   = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp",
  [string]$BuildDir  = "",
  [switch]$Reconfigure
)
$ErrorActionPreference = 'Stop'
if (-not $BuildDir) { $BuildDir = Join-Path $Project 'build' }

$toolsDir = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\tools"

# --- locate MSVC and import its environment -------------------------------
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsPath  = & $vswhere -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw "MSVC x64 build tools not found via vswhere" }
$vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found at $vcvars" }

Write-Host "Importing MSVC env from: $vcvars"
# Run vcvars in cmd, dump the resulting environment, and apply it to this session.
& cmd.exe /c "`"$vcvars`" >nul 2>&1 && set" | ForEach-Object {
  if ($_ -match '^([^=]+)=(.*)$') {
    Set-Item -Path ("Env:" + $matches[1]) -Value $matches[2] -ErrorAction SilentlyContinue
  }
}

# our local ninja wins over anything else on PATH
$env:PATH = "$toolsDir;$env:PATH"

Write-Host ("cl    : " + (Get-Command cl.exe    -EA SilentlyContinue).Source)
Write-Host ("ninja : " + (Get-Command ninja.exe -EA SilentlyContinue).Source)
Write-Host ("cmake : " + (Get-Command cmake.exe -EA SilentlyContinue).Source)

if ($Reconfigure -and (Test-Path $BuildDir)) {
  Write-Host "Removing existing build dir for a clean configure..."
  Remove-Item -Recurse -Force $BuildDir
}

Write-Host "`n=== CONFIGURE ===`n"
cmake -S $Project -B $BuildDir -G Ninja -DCMAKE_BUILD_TYPE=Release -DPSX_RECOMP_UI=OFF
if ($LASTEXITCODE -ne 0) { Write-Host "CONFIGURE FAILED ($LASTEXITCODE)"; exit $LASTEXITCODE }

Write-Host "`n=== BUILD ===`n"
cmake --build $BuildDir --config Release --parallel
$rc = $LASTEXITCODE
Write-Host "`n=== BUILD EXIT: $rc ==="
if ($rc -eq 0) {
  Get-ChildItem $BuildDir -Recurse -Filter "psx-runtime*.exe" -EA SilentlyContinue |
    ForEach-Object { "ARTIFACT: {0}  ({1:N1} MB)" -f $_.FullName, ($_.Length/1MB) }
}
exit $rc
