@echo off
cd /d "%~dp0"
python kaios.py
if errorlevel 1 (
  echo.
  echo KaiOS did not start. Please read README.txt for Python setup instructions.
  pause
)
