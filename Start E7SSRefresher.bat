@echo off
rem Double-click to open the E7SSRefresher control panel (no console window).
cd /d "%~dp0"
start "" pyw "%~dp0gui.py"
rem If the window never appears, run this instead to see errors:
rem   py gui.py
