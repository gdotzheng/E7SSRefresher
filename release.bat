@echo off
rem Build the exe and publish it as a GitHub Release in one step.
rem
rem Usage:  release.bat <version-tag> [release notes]
rem   e.g.  release.bat v1.0.1
rem         release.bat v1.0.1 "Fix refresh retry; faster scrolling"
rem
rem Requires the GitHub CLI (gh) to be installed and authenticated.
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: release.bat ^<version-tag^> [release notes]
  echo   e.g. release.bat v1.0.1 "what changed"
  exit /b 1
)
set "VERSION=%~1"
set "NOTES=%~2"
if "%NOTES%"=="" set "NOTES=E7SSRefresher %VERSION% - standalone Windows build. Run as admin (UAC). Settings in %%APPDATA%%\E7SSRefresher. Automating gameplay may violate Epic Seven's ToS."

echo === Building exe ===
call "%~dp0build.bat"
if not exist "dist\E7SSRefresher.exe" (
  echo Build failed - dist\E7SSRefresher.exe not found.
  exit /b 1
)

echo.
echo === Creating GitHub release %VERSION% ===
gh release create "%VERSION%" "dist\E7SSRefresher.exe" --title "E7SSRefresher %VERSION%" --notes "%NOTES%"
if errorlevel 1 (
  echo.
  echo Release create failed. If tag %VERSION% already exists, update its asset instead:
  echo   gh release upload "%VERSION%" "dist\E7SSRefresher.exe" --clobber
  exit /b 1
)
echo.
echo Done. Release %VERSION% published with E7SSRefresher.exe attached.
