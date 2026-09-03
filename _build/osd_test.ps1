$ErrorActionPreference="Continue"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class O {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out R r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h,ref P p);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk,byte sc,uint f,IntPtr e);
  public struct R { public int L,T,Rt,B; } public struct P { public int X,Y; }
}
"@
$proj="c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$env:PSX_DEV_INPUT="1"
$p=Start-Process -FilePath "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe" `
   -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
   -WorkingDirectory "$proj\build-clang" -PassThru `
   -RedirectStandardOutput "$env:TEMP\osd.log" -RedirectStandardError "$env:TEMP\osd2.log"
Start-Sleep -Seconds 48
$p.Refresh(); $h=$p.MainWindowHandle
if($h -eq [IntPtr]::Zero){ "no window"; $p.Kill(); exit 1 }
[void][O]::SetForegroundWindow($h); Start-Sleep -Milliseconds 900

# F5 = quick save (VK_F5 = 0x74)
[O]::keybd_event(0x74,0,0,[IntPtr]::Zero); Start-Sleep -Milliseconds 80
[O]::keybd_event(0x74,0,2,[IntPtr]::Zero)
Start-Sleep -Milliseconds 350          # toast lasts 1200ms - grab it mid-life

$rc=New-Object O+R; [void][O]::GetClientRect($h,[ref]$rc)
$pt=New-Object O+P; [void][O]::ClientToScreen($h,[ref]$pt)
$w=$rc.Rt-$rc.L; $ht=$rc.B-$rc.T
$b=New-Object System.Drawing.Bitmap $w,$ht
$g=[System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen($pt.X,$pt.Y,0,0,$b.Size)
# crop the top-left corner where toasts are drawn
$cw=[int]($w*0.42); $chh=[int]($ht*0.16)
$crop=New-Object System.Drawing.Bitmap $cw,$chh
$g2=[System.Drawing.Graphics]::FromImage($crop)
$g2.DrawImage($b,(New-Object System.Drawing.Rectangle 0,0,$cw,$chh),
              (New-Object System.Drawing.Rectangle 0,0,$cw,$chh),
              [System.Drawing.GraphicsUnit]::Pixel)
$crop.Save("c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\osd_quicksave.png",[System.Drawing.Imaging.ImageFormat]::Png)
"captured ${cw}x${chh} top-left corner"
$g.Dispose();$g2.Dispose();$b.Dispose();$crop.Dispose();$p.Kill();$p.WaitForExit()
