@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\Bridge Console.exe" (
  start "" "%~dp0dist\Bridge Console.exe"
) else (
  start "" pythonw "%~dp0bridge_console_qt.py"
)
exit /b 0
