@echo off
title PSX2PSP Enhanced – Python Edition
cd /d "%~dp0"

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Download Python 3.x from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Launch the app (pass through any command-line args)
python psx2psp_py\psx2psp.py %*

if errorlevel 1 (
    echo.
    echo Launch failed. If you see an ImportError, install dependencies:
    echo   pip install pillow requests yt-dlp tqdm mutagen
    echo.
    echo For popstation.dll ^(32-bit DLL^) support, also install 32-bit Python:
    echo   https://www.python.org/downloads/ ^(choose "Windows installer ^(32-bit^)"^)
    echo   Then set: set PYTHON32=C:\Python312-32\python.exe
    pause
)
