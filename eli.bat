@echo off
REM ELI v3 — Windows launcher
setlocal
set SCRIPT_DIR=%~dp0
set VENV=%SCRIPT_DIR%.venv

if not exist "%VENV%\Scripts\python.exe" (
    echo [ELI] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)

REM The Python this environment was built from may have been removed or upgraded since.
REM Mend it in place when that is possible, and say what to do when it is not.
"%VENV%\Scripts\python.exe" -c "import sys" >nul 2>&1
if not errorlevel 1 goto start_eli
set BOOT_PY=
where py >nul 2>&1
if not errorlevel 1 set BOOT_PY=py -3
if not defined BOOT_PY (
    where python >nul 2>&1
    if not errorlevel 1 set BOOT_PY=python
)
if not defined BOOT_PY (
    echo [ELI] ELI's Python environment cannot start, and no Python was found to repair it.
    echo       Install Python from https://www.python.org/downloads/ and run install.bat.
    pause
    exit /b 1
)
%BOOT_PY% "%SCRIPT_DIR%scripts\eli_env.py" repair "%SCRIPT_DIR%."
if errorlevel 1 (
    pause
    exit /b 1
)

:start_eli
"%VENV%\Scripts\python.exe" -m eli %*
