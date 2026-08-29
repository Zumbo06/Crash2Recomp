param([int]$Warm = 55)
$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$proj = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$exe  = "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe"

Add-Type @"
using System;using System.Runtime.InteropServices;
public class N {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RC r);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk,byte sc,uint f,IntPtr e);
  public struct RC { public int L,T,R,B; }
}
"@
function Shot($h,$path){
  $r = New-Object N+RC; [void][N]::GetWindowRect($h,[ref]$r)
  $b = New-Object System.Drawing.Bitmap (($r.R-$r.L)),(($r.B-$r.T))
  $g = [System.Drawing.Graphics]::FromImage($b)
  $g.CopyFromScreen($r.L,$r.T,0,0,$b.Size); $b.Save($path,[System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $b.Dispose()
}
function Key($vk,$times=1){
  for($i=0;$i -lt $times;$i++){
    [N]::keybd_event($vk,0,0,[IntPtr]::Zero); Start-Sleep -Milliseconds 90
    [N]::keybd_event($vk,0,2,[IntPtr]::Zero); Start-Sleep -Milliseconds 320
  }
}

$env:PSX_DEV_INPUT = "1"
$p = Start-Process -FilePath $exe -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
     -WorkingDirectory "$proj\build-clang" -RedirectStandardOutput "$env:TEMP\it_out.log" `
     -RedirectStandardError "$env:TEMP\it_err.log" -PassThru
Start-Sleep -Seconds $Warm
if ($p.HasExited) { "exited early 0x{0:X8}" -f $p.ExitCode; exit 1 }
$p.Refresh(); $h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { "no window"; $p.Kill(); exit 2 }

[void][N]::SetForegroundWindow($h); Start-Sleep -Milliseconds 900
$fg = [N]::GetForegroundWindow()
"focused ok: $($fg -eq $h)   (game hwnd=$h  foreground=$fg)"

Shot $h "$PSD\before.png"
Shot $h "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\in_before.png"
# Return = start (0x0D), X = cross (0x58), Down (0x28)
"sending: Return x2, Down x3, X x2"
Key 0x0D 2; Key 0x28 3; Key 0x58 2
Start-Sleep -Seconds 3
Shot $h "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\in_after.png"
"screenshots written"
$p.Kill(); $p.WaitForExit()
