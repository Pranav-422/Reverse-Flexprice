@echo off
rem Windows launcher for the browser view (same as ./scripts/ui on macOS/Linux).
rem Usage: scripts\ui
cd /d "%~dp0.."
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" scripts\ui.py %*
) else (
  python scripts\ui.py %*
)
