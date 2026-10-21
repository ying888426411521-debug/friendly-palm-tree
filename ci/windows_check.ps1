# Runs on the Windows build machine: starts dist\NetWatch.exe and checks that it
#  - keeps running,
#  - stores config.json under %APPDATA%\NetWatch (not next to the exe),
#  - embeds itself into the taskbar (child window of Shell_TrayWnd),
#  - re-embeds after explorer.exe is restarted.
# Taskbar screenshots are saved to ci-shots\ and the widget's pixels are printed to the log.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string n);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public static List<IntPtr> ChildrenOf(IntPtr parent, uint[] pids) {
    var found = new List<IntPtr>();
    EnumChildWindows(parent, (h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (Array.IndexOf(pids, pid) >= 0 && GetParent(h) == parent) found.Add(h);
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
"@
[void][W]::SetProcessDPIAware()
New-Item -ItemType Directory -Force ci-shots | Out-Null

function Get-Rect($h) { $r = New-Object W+RECT; [void][W]::GetWindowRect($h, [ref]$r); return $r }

function Save-TaskbarShot($name) {
  $bar = [W]::FindWindow("Shell_TrayWnd", $null)
  if ($bar -eq [IntPtr]::Zero) { Write-Host "no taskbar to screenshot"; return }
  $r = Get-Rect $bar
  $w = [Math]::Min(640, $r.R - $r.L); $h = $r.B - $r.T
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  try {
    $g.CopyFromScreen($r.R - $w, $r.T, 0, 0, $bmp.Size)
    $path = "ci-shots\$name.png"
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Host "saved $path"
  } catch { Write-Host "screenshot failed: $_" }
  finally { $g.Dispose(); $bmp.Dispose() }
}

# Prints the embedded window as characters so the result can be read from the log:
#   ' ' = same colour as the taskbar next to it (i.e. transparent)
#   '#' = NetWatch's own background colour (the colour key; should not appear)
#   'G' / 'Y' / 'R' = status dot,  '*' = text and other pixels
function Show-Pixels($label, $win) {
  $bar = [W]::FindWindow("Shell_TrayWnd", $null)
  $br = Get-Rect $bar
  $w = $br.R - $br.L; $h = $br.B - $br.T
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  try { $g.CopyFromScreen($br.L, $br.T, 0, 0, $bmp.Size) } catch { Write-Host "capture failed: $_"; return }
  $wr = Get-Rect $win
  $x0 = $wr.L - $br.L; $x1 = $wr.R - $br.L; $y0 = $wr.T - $br.T; $y1 = $wr.B - $br.T
  $ref = $bmp.GetPixel([Math]::Max(0, $x0 - 6), [int](($y0 + $y1) / 2))
  $light = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -ErrorAction SilentlyContinue).SystemUsesLightTheme
  $key = if ($light -eq 1) { [System.Drawing.Color]::FromArgb(0xee, 0xf1, 0xf5) } else { [System.Drawing.Color]::FromArgb(0x20, 0x20, 0x20) }
  Write-Host "[$label] light theme=$light taskbar sample=$($ref.R),$($ref.G),$($ref.B)"
  function Near($a, $b, $t) { [Math]::Abs($a.R - $b.R) + [Math]::Abs($a.G - $b.G) + [Math]::Abs($a.B - $b.B) -le $t }
  $keyCount = 0
  for ($y = $y0; $y -lt $y1; $y++) {
    $line = ""
    for ($x = [Math]::Max(0, $x0 - 4); $x -lt [Math]::Min($w, $x1 + 4); $x++) {
      $c = $bmp.GetPixel($x, $y)
      if (Near $c $ref 24) { $line += " " }
      elseif (Near $c $key 6) { $line += "#"; $keyCount++ }
      elseif ($c.G -gt 150 -and $c.R -lt 120 -and $c.B -lt 150) { $line += "G" }
      elseif ($c.R -gt 200 -and $c.G -gt 120 -and $c.B -lt 90) { $line += "Y" }
      elseif ($c.R -gt 200 -and $c.G -lt 110 -and $c.B -lt 110) { $line += "R" }
      else { $line += "*" }
    }
    Write-Host "|$line|"
  }
  Write-Host "[$label] colour-key pixels visible: $keyCount"
  $g.Dispose(); $bmp.Dispose()
}

function Assert-Embedded($label) {
  $bar = [W]::FindWindow("Shell_TrayWnd", $null)
  if ($bar -eq [IntPtr]::Zero) { throw "[$label] no Shell_TrayWnd on this machine" }
  $pids = [uint32[]]@(Get-Process NetWatch | ForEach-Object { $_.Id })
  $kids = [W]::ChildrenOf($bar, $pids)
  $br = Get-Rect $bar
  Write-Host "[$label] taskbar: $($br.L),$($br.T) - $($br.R),$($br.B); NetWatch pids: $pids"
  if ($kids.Count -eq 0) { throw "[$label] NetWatch is not embedded in the taskbar" }
  foreach ($k in $kids) {
    $r = Get-Rect $k
    Write-Host "[$label] embedded window: $($r.L),$($r.T) - $($r.R),$($r.B) visible=$([W]::IsWindowVisible($k))"
    if ($r.T -lt $br.T -or $r.B -gt $br.B) { throw "[$label] embedded window is outside the taskbar" }
    Show-Pixels $label $k
  }
}

$cfg = Join-Path $env:APPDATA "NetWatch\config.json"
Remove-Item $cfg -ErrorAction SilentlyContinue
$p = Start-Process dist\NetWatch.exe -PassThru
try {
  Start-Sleep -Seconds 15
  if ($p.HasExited) { throw "NetWatch.exe exited early with code $($p.ExitCode)" }
  if (-not (Test-Path $cfg)) { throw "config.json was not created in $cfg" }
  if (Test-Path dist\config.json) { throw "config.json must not be created next to the exe" }
  Write-Host "config OK: $cfg"

  Assert-Embedded "start"
  Save-TaskbarShot "start"

  Write-Host "restarting explorer.exe ..."
  Stop-Process -Name explorer -Force
  Start-Sleep -Seconds 5
  if (-not (Get-Process explorer -ErrorAction SilentlyContinue)) { Start-Process explorer.exe }
  Start-Sleep -Seconds 20
  if ($p.HasExited) { throw "NetWatch.exe exited after explorer restart (code $($p.ExitCode))" }
  Assert-Embedded "after explorer restart"
  Save-TaskbarShot "after-restart"
}
finally {
  Get-Process NetWatch -ErrorAction SilentlyContinue | Stop-Process -Force
}
