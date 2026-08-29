param([int]$Wait = 40, [string]$Out = "$env:TEMP\shot.png", [string]$ExtraArgs = "")
$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Drawing
$proj = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$exe  = "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe"
$argList = @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl')
if ($ExtraArgs) { $argList += $ExtraArgs.Split(' ') }

$p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory "$proj\build-clang" `
     -RedirectStandardOutput "$env:TEMP\s_out.log" -RedirectStandardError "$env:TEMP\s_err.log" `
     -PassThru -NoNewWindow
Start-Sleep -Seconds $Wait
if ($p.HasExited) { "process exited 0x{0:X8} before screenshot" -f $p.ExitCode; exit 1 }

# Find the game window and grab its client area.
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern IntPtr FindWindowEx(IntPtr p,IntPtr c,string cls,string win);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out R r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public struct R { public int L,T,Rt,B; }
}
"@
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { $p.Refresh(); $h = $p.MainWindowHandle }
if ($h -eq [IntPtr]::Zero) { "no window handle"; $p.Kill(); exit 2 }
[void][W]::SetForegroundWindow($h); Start-Sleep -Milliseconds 700
$r = New-Object W+R
[void][W]::GetWindowRect($h,[ref]$r)
$w = $r.Rt - $r.L; $ht = $r.B - $r.T
"window: $w x $ht at $($r.L),$($r.T)"
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"saved: $Out"
$p.Kill(); $p.WaitForExit()
