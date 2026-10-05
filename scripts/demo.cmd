@echo off
rem Windows launcher for the live demo (same as ./scripts/demo on macOS/Linux).
rem Usage: scripts\demo
cd /d "%~dp0.."
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" scripts\demo.py %*
) else (
  python scripts\demo.py %*
)
