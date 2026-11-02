@echo off
REM Build a single NetWatch.exe into dist\ (requires: pip install pyinstaller certifi)
cd /d "%~dp0"
pyinstaller --noconsole --onefile --name NetWatch --icon icon.ico netwatch.py
pause
