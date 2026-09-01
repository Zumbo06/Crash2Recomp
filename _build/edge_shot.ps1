param([int]$Wait = 50)
$ErrorActionPreference="Continue"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class E {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out R r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h,ref P p);
  public struct R { public int L,T,Rt,B; } public struct P { public int X,Y; }
}
"@
$proj="c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$env:PSX_DEV_INPUT="1"
$p=Start-Process -FilePath "$proj\build-debugtools\Crash_Bandicoot_2_Recompiled.exe" `
   -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
   -WorkingDirectory "$proj\build-debugtools" -PassThru `
   -RedirectStandardOutput "$env:TEMP\es.log" -RedirectStandardError "$env:TEMP\es2.log"
Start-Sleep -Seconds $Wait
$p.Refresh(); $h=$p.MainWindowHandle
if($h -eq [IntPtr]::Zero){ "no window"; $p.Kill(); exit 1 }
[void][E]::SetForegroundWindow($h); Start-Sleep -Milliseconds 900
$rc=New-Object E+R; [void][E]::GetClientRect($h,[ref]$rc)
$pt=New-Object E+P; [void][E]::ClientToScreen($h,[ref]$pt)
$w=$rc.Rt-$rc.L; $ht=$rc.B-$rc.T
"client ${w}x${ht} at $($pt.X),$($pt.Y)"
$b=New-Object System.Drawing.Bitmap $w,$ht
$g=[System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen($pt.X,$pt.Y,0,0,$b.Size)
$b.Save("c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\edge_full.png",[System.Drawing.Imaging.ImageFormat]::Png)
# crop the rightmost 12% so the artifact is actually visible when I look at it
$cw=[int]($w*0.12)
$crop=New-Object System.Drawing.Bitmap $cw,$ht
$g2=[System.Drawing.Graphics]::FromImage($crop)
$g2.DrawImage($b,(New-Object System.Drawing.Rectangle 0,0,$cw,$ht),
              (New-Object System.Drawing.Rectangle ($w-$cw),0,$cw,$ht),
              [System.Drawing.GraphicsUnit]::Pixel)
$crop.Save("c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\edge_right.png",[System.Drawing.Imaging.ImageFormat]::Png)
"saved edge_full.png and edge_right.png (rightmost ${cw}px)"
$g.Dispose();$g2.Dispose();$b.Dispose();$crop.Dispose();$p.Kill();$p.WaitForExit()
