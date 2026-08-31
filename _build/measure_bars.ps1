param([int]$Wait = 45, [string]$Out = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\bars_measure.png")
$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Drawing
$proj = "c:\Users\yhgoz\Desktop\Crash2_Rcomp\_build\Crash2Recomp"
Add-Type @"
using System;using System.Runtime.InteropServices;using System.Text;
public class M {
  [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c,string w);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h,out RC r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h,ref PT p);
  public struct RC { public int L,T,R,B; }
  public struct PT { public int X,Y; }
}
"@
$env:PSX_DEV_INPUT="1"; $env:PSX_SCALING_MODE="fill"
$p = Start-Process -FilePath "$proj\build-clang\Crash_Bandicoot_2_Recompiled.exe" `
     -ArgumentList @('--no-launcher','--game',"$proj\game.toml",'--renderer','opengl') `
     -WorkingDirectory "$proj\build-clang" -PassThru `
     -RedirectStandardOutput "$env:TEMP\mb.log" -RedirectStandardError "$env:TEMP\mb2.log"
Start-Sleep -Seconds $Wait
$h = [M]::FindWindow($null, "Crash Bandicoot 2 Recompiled")
if ($h -eq [IntPtr]::Zero) { "window not found"; $p.Kill(); exit 2 }
[void][M]::SetForegroundWindow($h); Start-Sleep -Milliseconds 800
$rc = New-Object M+RC; [void][M]::GetClientRect($h,[ref]$rc)
$pt = New-Object M+PT; $pt.X=0; $pt.Y=0; [void][M]::ClientToScreen($h,[ref]$pt)
$w = $rc.R - $rc.L; $ht = $rc.B - $rc.T
"client: ${w}x${ht} at $($pt.X),$($pt.Y)"
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($pt.X, $pt.Y, 0, 0, $bmp.Size)
$bmp.Save($Out,[System.Drawing.Imaging.ImageFormat]::Png)

# count near-black rows from top and bottom, sampling across the width
function RowIsBlack($bmp,$y,$w){
  for($x=[int]($w*0.15); $x -lt [int]($w*0.85); $x += [int]($w/40)){
    $c = $bmp.GetPixel($x,$y)
    if (($c.R + $c.G + $c.B) -gt 24) { return $false }
  }
  return $true
}
$top = 0; while ($top -lt $ht/3 -and (RowIsBlack $bmp $top $w)) { $top++ }
$bot = 0; while ($bot -lt $ht/3 -and (RowIsBlack $bmp ($ht-1-$bot) $w)) { $bot++ }
"black rows: top=$top bottom=$bot of $ht"
"in PS1 240ths: top=$([math]::Round($top*240.0/$ht,1)) bottom=$([math]::Round($bot*240.0/$ht,1))"
$g.Dispose(); $bmp.Dispose()
$p.Kill(); $p.WaitForExit()
