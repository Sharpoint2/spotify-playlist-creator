@echo off
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage:
  echo   run_cli_windows.bat --input songs.txt --playlist-name "My Playlist" --client-id YOUR_ID --client-secret YOUR_SECRET --redirect-uri http://127.0.0.1:8888/callback
  echo.
  echo Tip: You can pass any create_spotify_playlist.py arguments through this launcher.
  pause
  exit /b 1
)

where py >nul 2>&1
if %errorlevel%==0 (
  py -3 create_spotify_playlist.py %*
  goto end
)

where python >nul 2>&1
if %errorlevel%==0 (
  python create_spotify_playlist.py %*
  goto end
)

echo Python was not found on PATH.
echo Install Python 3 from https://www.python.org/downloads/windows/
echo and check "Add python.exe to PATH" during setup.

:end
pause
