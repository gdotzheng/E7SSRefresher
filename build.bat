@echo off
rem Build E7SSRefresher.exe (single file, requests admin via UAC, no console).
rem Output: dist\E7SSRefresher.exe  — templates are baked in; config.json is created
rem next to the .exe on first run and is editable afterwards.
cd /d "%~dp0"
py -m pip install --quiet pyinstaller
py -m PyInstaller --noconfirm --onefile --windowed --uac-admin --name E7SSRefresher ^
  --add-data "templates;templates" ^
  --add-data "config.json;." ^
  --collect-all windows_capture ^
  gui.py
echo.
echo Done. EXE at: dist\E7SSRefresher.exe
