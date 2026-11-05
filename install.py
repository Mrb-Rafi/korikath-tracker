"""Set up the tracker on this PC (run again if you move this folder):

  - "Korikath Tracker" shortcut on the Desktop and in the Start menu, which opens the dashboard
  - Windows scheduled tasks: full crawl every Monday, quick check the other days, both 9:00 AM,
    and the view counts every day at 11:00 AM;
    each followed by an on-wiki usage check

To pin the shortcut to the taskbar, right-click it in the Start menu > Pin to taskbar.
"""

import math
import struct
import subprocess
import sys
import zlib
from pathlib import Path

BASE = Path(__file__).resolve().parent
ICON = BASE / "korikath.ico"
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
SCHEDULE_TIME = "9:00AM"
VIEWS_TIME = "11:00AM"  # after the morning crawl

# Same branch colours as the dashboard's tree map.
PALETTE = [(0x4e, 0x79, 0xa7), (0xf2, 0x8e, 0x2b), (0x59, 0xa1, 0x4f), (0xe1, 0x57, 0x59),
           (0x76, 0xb7, 0xb2), (0xed, 0xc9, 0x48), (0xb0, 0x7a, 0xa1)]


def sunburst_png(size=256):
    """A small sunburst (the tree map's shape) as a PNG, drawn pixel by pixel with no libraries."""
    c = (size - 1) / 2
    rings = [(0.00, 0.22, None), (0.26, 0.62, 5), (0.65, 0.98, 9)]  # (inner, outer, slices)
    # Uneven slice sizes so it reads as "some branches are bigger".
    weights = {5: [5, 3, 2, 1.4, 1], 9: [3, 2, 1.6, 1.2, 1, 1, 0.8, 0.7, 0.6]}
    rows = []
    for y in range(size):
        row = bytearray([0])  # PNG filter type: none
        for x in range(size):
            dx, dy = (x - c) / c, (y - c) / c
            r = math.hypot(dx, dy)
            px = (0, 0, 0, 0)
            for inner, outer, n in rings:
                if inner <= r <= outer:
                    if n is None:
                        px = (0x2f, 0x6b, 0x4f, 255)  # centre: the mother category
                    else:
                        a = (math.atan2(dy, dx) + math.pi / 2) % (2 * math.pi) / (2 * math.pi)
                        w = weights[n]
                        total, acc, i = sum(w), 0, 0
                        while i < n - 1 and a > (acc + w[i]) / total:
                            acc += w[i]
                            i += 1
                        edge = min(a - acc / total, (acc + w[i]) / total - a) * 2 * math.pi * r * c
                        if edge < 1.6:  # thin gap between slices
                            break
                        col = PALETTE[(i * (2 if n == 9 else 1)) % len(PALETTE)]
                        if n == 9:
                            col = tuple(int(v + (255 - v) * 0.3) for v in col)
                        px = (*col, 255)
                    break
            row += bytes(px)
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def write_icon():
    png = sunburst_png(256)
    # ICO with one 256x256 PNG image (Windows scales it down for small sizes).
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    ICON.write_bytes(header + entry + png)


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
foreach ($path in @("$desktop\Korikath Tracker.lnk", "$programs\Korikath Tracker.lnk")) {{
  $s = $shell.CreateShortcut($path)
  $s.TargetPath = '{PYTHONW}'
  $s.Arguments = '"{server}"'
  $s.WorkingDirectory = '{BASE}'
  $s.IconLocation = '{ICON},0'
  $s.Description = 'Open the Project Korikath dashboard'
  $s.Save()
  Write-Output "  $path"
}}
# Older versions also had a dashboard-only shortcut; the main one now does that.
if (Test-Path "$programs\Korikath Dashboard.lnk") {{ Remove-Item "$programs\Korikath Dashboard.lnk" }}
"""))


def make_schedule():
    """Full crawl every Monday, quick check the other six days, both at SCHEDULE_TIME, each followed
    by an on-wiki usage check; and every day at VIEWS_TIME the view-count job (its own lock, so it never
    blocks a crawl). Missed runs (PC off) start as soon as the PC is on; locks stop two from running at once."""
    print("Scheduled tasks:\n" + powershell(rf"""
$ErrorActionPreference = 'Stop'
# The single weekly task from older versions is replaced by these.
Unregister-ScheduledTask -TaskName 'Korikath Tracker (weekly)' -Confirm:$false -ErrorAction SilentlyContinue
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
Task 'Korikath Tracker (weekly full crawl)' '-u tracker.py --full --usage --log logs\scheduled.log' @('Monday') '{SCHEDULE_TIME}' 2 'Full crawl of Category:Project Korikath; updates the dashboard'
Task 'Korikath Tracker (daily quick check)' '-u tracker.py --quick --usage --log logs\scheduled.log' $every[1..6] '{SCHEDULE_TIME}' 2 'Quick check of Category:Project Korikath; updates the dashboard'
# Usually about 50 minutes; longer only while it finishes the one-time history of all files.
Task 'Korikath Tracker (daily views)' '-u tracker.py --views --log logs\views.log' $every '{VIEWS_TIME}' 23 'View counts of Korikath files (1/30 of the files a day); updates the dashboard'
"""))


if __name__ == "__main__":
    write_icon()
    print(f"Icon: {ICON}")
    make_shortcuts()
    make_schedule()
