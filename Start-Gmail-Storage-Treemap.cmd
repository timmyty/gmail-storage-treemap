@echo off
setlocal
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pythonw.exe "%~dp0gmail_storage_treemap.py"
  exit /b
)
where pyw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pyw.exe -3 "%~dp0gmail_storage_treemap.py"
  exit /b
)
echo Gmail Storage Treemap needs Python 3.10 or later with Tkinter.
echo Install Python from https://www.python.org/downloads/windows/
echo Then double-click this file again. No extra packages are needed.
pause
