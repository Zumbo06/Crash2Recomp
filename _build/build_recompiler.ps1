# Rebuild psxrecomp-game from the VENDORED tree.
#
# The prebuilt psxrecomp-cli binaries were built from unpatched emitter sources.
# Our patch 0002 (dllexport for clang) edits cpu_state.h and psx_cycles.h, both
# of which are in codegen_hash_sources.cmake - so the vendored tree stamps a
# different codegen hash than the shipped psxrecomp-game.exe bakes, and
# compile_overlays.py's staleness guard refuses to emit shards. Result: every
# overlay falls back to the MIPS interpreter.
#
# Building the recompiler from the same tree makes both sides agree.
$ErrorActionPreference = "Stop"
$packRoot = "c:\Users\yhgoz\.local\share\retcomm\toolchains\cmake-clang-v1"
$T = Get-ChildItem $packRoot -Directory | Sort-Object Name -Descending |
     Select-Object -First 1 -ExpandProperty FullName
if (-not $T) { throw "clang toolchain not found under $packRoot" }
$env:PATH = "$T\bin;$env:PATH"

$Src = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp\psxrecomp\recompiler"
$Dir = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\build-recompiler"

# STATIC LINK, and this is not optional: this binary SHIPS to players.
#
# Built without it, clang links against libc++.dll and libunwind.dll from the
# toolchain's own bin directory. Those resolve on this machine because the
# toolchain is on PATH, and on nobody else's - so the release bundle failed at
# "[2/4] Recompiling game executable" with only "game recompilation failed" to
# show for it, because the process could not start at all. The stock CLI binary
# it replaces imports KERNEL32 and msvcrt and nothing else; ours must match
# that bar. The import check at the end of this script enforces it.
& "$T\bin\cmake.exe" -S $Src -B $Dir -G Ninja `
  -DCMAKE_BUILD_TYPE=Release -DPSXRECOMP_ENABLE_CHD=OFF -DBUILD_TESTING=OFF `
  -DCMAKE_C_COMPILER="$T/bin/clang.exe" `
  -DCMAKE_CXX_COMPILER="$T/bin/clang++.exe" `
  -DCMAKE_EXE_LINKER_FLAGS="-static -static-libgcc -static-libstdc++"
if ($LASTEXITCODE -ne 0) { throw "configure failed" }

& "$T\bin\cmake.exe" --build $Dir --target psxrecomp-game
if ($LASTEXITCODE -ne 0) { throw "build failed" }

$built = Get-ChildItem $Dir -Recurse -Filter psxrecomp-game.exe |
         Select-Object -First 1 -ExpandProperty FullName
Write-Host "built: $built"
Write-Host ("codegen hash: " + (& $built --codegen-hash | Select-Object -First 1))

# Self-containment gate. Anything outside this list means the binary needs a
# DLL the player does not have.
$allowed = '^(KERNEL32|USER32|ADVAPI32|msvcrt|api-ms-win-crt-[a-z0-9-]+)\.dll$'
$imports = & "$T\bin\llvm-objdump.exe" -p $built |
           Select-String 'DLL Name: (.+)' |
           ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() } | Sort-Object -Unique
$bad = $imports | Where-Object { $_ -notmatch $allowed }
if ($bad) {
  Write-Host "imports: $($imports -join ', ')" -ForegroundColor Red
  throw "psxrecomp-game.exe is NOT self-contained; it needs: $($bad -join ', ')"
}
Write-Host "imports OK (system only): $($imports -join ', ')" -ForegroundColor Green
