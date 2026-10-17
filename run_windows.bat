@echo off
REM Double-click to start NetWatch without a console window
cd /d "%~dp0"
where pythonw >/dev/null 2>/dev/null && (start "" pythonw netwatch.py) || (start "" pyw netwatch.py)
