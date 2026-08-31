param([string]$Crop = "0,0,0,0")
$ErrorActionPreference="Continue"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W2 {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out R r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h,ref P p);
  public struct R { public int L,T,Rt,B; } public struct P { public int X,Y; }
}
"@
$proj="c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
$env:PSX_DEV_INPUT="1"; $env:PSX_SCALING_MODE="fill"; $env:PSX_OVERSCAN_CROP=$Crop
$p=Start-Process -FilePath "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe" `
   -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
   -WorkingDirectory "$proj\build-clang" -PassThru `
   -RedirectStandardOutput "$env:TEMP\m.log" -RedirectStandardError "$env:TEMP\m2.log"
Start-Sleep -Seconds 42
$p.Refresh(); $h=$p.MainWindowHandle
if($h -eq [IntPtr]::Zero){ "no window"; $p.Kill(); exit 1 }
[void][W2]::SetForegroundWindow($h); Start-Sleep -Milliseconds 900
$rc=New-Object W2+R; [void][W2]::GetClientRect($h,[ref]$rc)
$pt=New-Object W2+P; [void][W2]::ClientToScreen($h,[ref]$pt)
$w=$rc.Rt-$rc.L; $ht=$rc.B-$rc.T
$b=New-Object System.Drawing.Bitmap $w,$ht
$g=[System.Drawing.Graphics]::FromImage($b)
$g.CopyFromScreen($pt.X,$pt.Y,0,0,$b.Size)
function Blk($bm,$y,$wd){ for($x=[int]($wd*0.2);$x -lt [int]($wd*0.8);$x+=[int]($wd/30)){
  $c=$bm.GetPixel($x,$y); if(($c.R+$c.G+$c.B) -gt 30){return $false} } return $true }
$t=0; while($t -lt $ht/3 -and (Blk $b $t $w)){$t++}
$bo=0; while($bo -lt $ht/3 -and (Blk $b ($ht-1-$bo) $w)){$bo++}
"crop=$Crop  client=${w}x${ht}  BLACK ROWS: top=$t bottom=$bo"
$g.Dispose(); $b.Dispose(); $p.Kill(); $p.WaitForExit()
