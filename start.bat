@echo off
rem ---------------------------------------------------------------
rem  HD2 Mod Updater - launcher (Chinese GUI).
rem  Keep this file ASCII-only: cmd.exe parses .bat in the OEM
rem  codepage, so UTF-8 Chinese here would break parsing.
rem ---------------------------------------------------------------
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"
title HD2 Mod Updater

set "PY="
where py.exe >nul 2>nul && set "PY=py.exe -3"
if not defined PY (
  where python.exe >nul 2>nul && set "PY=python.exe"
)
if not defined PY goto nopython

%PY% "run.py" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [X] Exited with code %RC%. See the message above.
  echo.
  pause >nul
)
exit /b %RC%

:nopython
echo [X] Python not found.
echo     Install Python 3.9+ from https://www.python.org/downloads/
echo     and tick "Add python.exe to PATH" during setup.
echo.
pause >nul
exit /b 2
