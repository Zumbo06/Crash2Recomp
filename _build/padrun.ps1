$ErrorActionPreference="Continue"
$proj="c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$p=Start-Process -FilePath "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe" `
   -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
   -WorkingDirectory "$proj\build-clang" -RedirectStandardOutput "$env:TEMP\pr_out.log" `
   -RedirectStandardError "$env:TEMP\pr_err.log" -PassThru
Start-Sleep -Seconds 35
if(-not $p.HasExited){ "running ok"; $p.Kill(); $p.WaitForExit() } else { "exited 0x{0:X8}" -f $p.ExitCode }
