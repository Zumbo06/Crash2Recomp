param([int]$Wait = 6, [string]$Out = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\launcher.png", [string]$Page = "")
$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class L {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RC r);
  public struct RC { public int L,T,R,B; }
}
"@
$p = Start-Process -FilePath "python" -ArgumentList @("main.py") `
     -WorkingDirectory "c:\Users\yhgoz\Desktop\Crash2_Rcomp\launcher" `
     -RedirectStandardError "$env:TEMP\lch_err.log" -RedirectStandardOutput "$env:TEMP\lch_out.log" `
     -PassThru
Start-Sleep -Seconds $Wait
$p.Refresh()
if ($p.HasExited) { "launcher exited early ($($p.ExitCode))"; Get-Content "$env:TEMP\lch_err.log" -Tail 20; exit 1 }
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { "no window handle"; $p.Kill(); exit 2 }
[void][L]::SetForegroundWindow($h); Start-Sleep -Milliseconds 700
$r = New-Object L+RC; [void][L]::GetWindowRect($h,[ref]$r)
$b = New-Object System.Drawing.Bitmap (($r.R-$r.L)),(($r.B-$r.T))
$g = [System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen($r.L,$r.T,0,0,$b.Size)
$b.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $b.Dispose()
"saved $Out ($($r.R-$r.L)x$($r.B-$r.T))"
$p.Kill(); $p.WaitForExit()
