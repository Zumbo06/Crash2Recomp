$ErrorActionPreference="Continue"
$proj="c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$env:PSX_DEV_INPUT="1"; $env:PSX_RB_PAD_TRACE="1"; $env:PSX_RB_PAD_LOG="1"; $env:PSX_DEVTRACE="1"
$p=Start-Process -FilePath "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe" `
   -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
   -WorkingDirectory "$proj\build-clang" -RedirectStandardOutput "$env:TEMP\pt_out.log" `
   -RedirectStandardError "$env:TEMP\pt_err.log" -PassThru
Start-Sleep -Seconds 50
if(-not $p.HasExited){ "running"; $p.Kill(); $p.WaitForExit() } else { "exited 0x{0:X8}" -f $p.ExitCode }
