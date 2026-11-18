"""Set up Reoa on this PC (run again if you move this folder):

  - "Reoa" shortcut on the Desktop and in the Start menu, which opens the dashboard
  - Windows scheduled tasks: full crawl every Monday, quick check the other days, both 9:00 AM,
    and the view counts every day at 11:00 AM;
    each followed by an on-wiki usage check

To pin the shortcut to the taskbar, right-click it in the Start menu > Pin to taskbar.
"""

import struct
import subprocess
import sys
import zlib
from pathlib import Path

from brand import MARK_BARS

BASE = Path(__file__).resolve().parent
ICON = BASE / "reoa.ico"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
SCHEDULE_TIME = "9:00AM"
VIEWS_TIME = "11:00AM"  # after the morning crawl

ICON_SIZES = [16, 32, 48, 64, 256]  # multiples of 16, so every bar stays one even block of pixels


def mark_png(size):
    """Reoa's mark (brand.py) as a PNG of size x size pixels, drawn with no libraries."""
    rows = []
    for y in range(size):
        gy = y * 16 // size
        row = bytearray([0])  # PNG filter type: none
        for x in range(size):
            gx = x * 16 // size
            white = any(x0 <= gx < x1 and gy == r for x0, x1, r in MARK_BARS)
            row += bytes((255, 255, 255, 255) if white else (0, 0, 0, 255))
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def write_icon():
    """ICO with the mark drawn at each of ICON_SIZES, so Windows never has to blur it by scaling."""
    pngs = [mark_png(s) for s in ICON_SIZES]
    out, offset = struct.pack("<HHH", 0, 1, len(pngs)), 6 + 16 * len(pngs)
    for s, png in zip(ICON_SIZES, pngs):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    ICON.write_bytes(out + b"".join(pngs))


def powershell(script):
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(out.stderr)
    return out.stdout.rstrip()


def make_shortcuts():
    server = BASE / "server.py"
    print("Shortcuts:\n" + powershell(rf"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
foreach ($path in @("$desktop\Reoa.lnk", "$programs\Reoa.lnk")) {{
  $s = $shell.CreateShortcut($path)
  $s.TargetPath = '{PYTHONW}'
  $s.Arguments = '"{server}"'
  $s.WorkingDirectory = '{BASE}'
  $s.IconLocation = '{ICON},0'
  $s.Description = 'Reoa: open the Project Korikath dashboard'
  $s.Save()
  Write-Output "  $path"
}}
# Older versions also had a dashboard-only shortcut; the main one now does that.
if (Test-Path "$programs\Korikath Dashboard.lnk") {{ Remove-Item "$programs\Korikath Dashboard.lnk" }}
# Before the tool was named Reoa, its shortcuts were called "Korikath Tracker".
foreach ($old in @("$desktop\Korikath Tracker.lnk", "$programs\Korikath Tracker.lnk")) {{ if (Test-Path $old) {{ Remove-Item $old }} }}
"""))


def make_schedule():
    """Full crawl every Monday, quick check the other six days, both at SCHEDULE_TIME, each followed
    by an on-wiki usage check; and every day at VIEWS_TIME the view-count job (its own lock, so it never
    blocks a crawl). Missed runs (PC off) start as soon as the PC is on; locks stop two from running at once."""
    print("Scheduled tasks:\n" + powershell(rf"""
$ErrorActionPreference = 'Stop'
# Tasks from before the tool was named Reoa ("Korikath Tracker ...") are replaced by these.
Get-ScheduledTask -TaskName 'Korikath Tracker*' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
function Task($name, $arguments, $days, $at, $hours, $desc) {{
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours $hours) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
  $action = New-ScheduledTaskAction -Execute '{PYTHONW}' -Argument $arguments -WorkingDirectory '{BASE}'
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $at
  Register-ScheduledTask -TaskName $name -Description $desc -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  $next = (Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo).NextRunTime
  Write-Output "  $name - next run $next"
}}
$every = @('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
Task 'Reoa (weekly full crawl)' '-u tracker.py --full --usage --log logs\scheduled.log' @('Monday') '{SCHEDULE_TIME}' 2 'Full crawl of Category:Project Korikath; updates the dashboard'
Task 'Reoa (daily quick check)' '-u tracker.py --quick --usage --log logs\scheduled.log' $every[1..6] '{SCHEDULE_TIME}' 2 'Quick check of Category:Project Korikath; updates the dashboard'
# Usually about 50 minutes; longer only while it finishes the one-time history of all files.
Task 'Reoa (daily views)' '-u tracker.py --views --log logs\views.log' $every '{VIEWS_TIME}' 23 'View counts of Korikath files (1/30 of the files a day); updates the dashboard'
"""))


if __name__ == "__main__":
    write_icon()
    print(f"Icon: {ICON}")
    make_shortcuts()
    make_schedule()
