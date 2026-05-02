@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
  py -3 spotify_playlist_creator_gui.py
  goto end
)

where python >nul 2>&1
if %errorlevel%==0 (
  python spotify_playlist_creator_gui.py
  goto end
)

echo Python was not found on PATH.
echo Install Python 3 from https://www.python.org/downloads/windows/
echo and check "Add python.exe to PATH" during setup.

:end
pause
