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
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, IntPtr e);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  public static List<IntPtr> VisibleTopLevelOf(uint[] pids) {
    var found = new List<IntPtr>();
    EnumWindows((h, l) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      RECT r; GetWindowRect(h, out r);
      if (Array.IndexOf(pids, pid) >= 0 && IsWindowVisible(h) && r.B - r.T > 60) found.Add(h);
      return true;
    }, IntPtr.Zero);
    return found;
  }
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
#   ' ' = same colour as the taskbar next to it (background blends in)
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
  Write-Host "[$label] light theme=$light taskbar sample=$($ref.R),$($ref.G),$($ref.B)"
  function Near($a, $b, $t) { [Math]::Abs($a.R - $b.R) + [Math]::Abs($a.G - $b.G) + [Math]::Abs($a.B - $b.B) -le $t }
  $bgCount = 0
  for ($y = $y0; $y -lt $y1; $y++) {
    $line = ""
    for ($x = [Math]::Max(0, $x0 - 4); $x -lt [Math]::Min($w, $x1 + 4); $x++) {
      $c = $bmp.GetPixel($x, $y)
      if (Near $c $ref 24) { $line += " "; $bgCount++ }
      elseif ($c.G -gt 150 -and $c.R -lt 120 -and $c.B -lt 150) { $line += "G" }
      elseif ($c.R -gt 200 -and $c.G -gt 120 -and $c.B -lt 90) { $line += "Y" }
      elseif ($c.R -gt 200 -and $c.G -lt 110 -and $c.B -lt 110) { $line += "R" }
      else { $line += "*" }
    }
    Write-Host "|$line|"
  }
  $total = ($y1 - $y0) * ([Math]::Min($w, $x1 + 4) - [Math]::Max(0, $x0 - 4))
  Write-Host "[$label] pixels matching the taskbar colour: $bgCount of $total"
  $g.Dispose(); $bmp.Dispose()
}

function Click($x, $y) {
  [void][W]::SetCursorPos($x, $y)
  Start-Sleep -Milliseconds 100
  [W]::mouse_event(0x0002, 0, 0, 0, [IntPtr]::Zero)
  Start-Sleep -Milliseconds 60
  [W]::mouse_event(0x0004, 0, 0, 0, [IntPtr]::Zero)
  [void][W]::SetCursorPos(5, 5)  # move away so hover highlight does not stick
  Start-Sleep -Milliseconds 1500
}

function NetWatch-Pids { [uint32[]]@(Get-Process NetWatch | ForEach-Object { $_.Id }) }

function Embedded-Window {
  $bar = [W]::FindWindow("Shell_TrayWnd", $null)
  $kids = [W]::ChildrenOf($bar, (NetWatch-Pids))
  if ($kids.Count -ne 1) { throw "expected exactly one embedded window, found $($kids.Count)" }
  return $kids[0]
}

# Clicks on the embedded widget must reach it (it lives inside explorer's taskbar window)
function Test-Clicks {
  $win = Embedded-Window
  $r = Get-Rect $win; $mid = [int](($r.T + $r.B) / 2); $w0 = $r.R - $r.L
  Click ($r.L + 6) $mid                       # the ‹ › toggle -> collapse
  $r1 = Get-Rect (Embedded-Window); $w1 = $r1.R - $r1.L
  Write-Host "[clicks] width expanded=$w0 after toggle=$w1 (right edge $($r.R) -> $($r1.R))"
  if ($w1 -ge $w0) { throw "[clicks] clicking the toggle did not collapse the widget" }
  if ($r1.R -ne $r.R) { throw "[clicks] right edge moved while collapsing" }
  Show-Pixels "collapsed" (Embedded-Window)
  Click ($r1.L + 6) $mid                      # toggle again -> expand
  $w2 = (Get-Rect (Embedded-Window)).R - (Get-Rect (Embedded-Window)).L
  if ($w2 -ne $w0) { throw "[clicks] clicking the toggle again did not expand the widget ($w2 vs $w0)" }

  Click ($r.L + 27) $mid                      # the status dot -> details popup
  $pops = [W]::VisibleTopLevelOf((NetWatch-Pids))
  if ($pops.Count -lt 1) { throw "[clicks] clicking the dot did not open the details popup" }
  $pr = Get-Rect $pops[0]
  Write-Host "[clicks] popup: $($pr.L),$($pr.T) - $($pr.R),$($pr.B)"
  if ($pr.B -gt $r.T) { throw "[clicks] popup should open above the taskbar" }
  Click ([int](($pr.L + $pr.R) / 2)) ([int](($pr.T + $pr.B) / 2))   # click popup -> close
  if ([W]::VisibleTopLevelOf((NetWatch-Pids)).Count -ne 0) { throw "[clicks] popup did not close" }
  Write-Host "[clicks] toggle and popup OK"
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
  Test-Clicks

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
