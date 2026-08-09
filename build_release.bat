@echo off
:: ============================================================================
:: build_release.bat — Build PSX2PSP Enhanced release package
::
:: Usage:  build_release.bat [VERSION]
::   e.g.  build_release.bat 1.1.2
::
:: Requires: Python 3.9+, PyInstaller, UPX in PATH
:: ============================================================================
setlocal

if "%1"=="" (
    echo Usage: build_release.bat VERSION
    echo   e.g. build_release.bat 1.1.2
    pause & exit /b 1
)
set "VER=%1"
set "REL=dist\release\PSX2PSP_Enhanced_v%VER%"
set "ZIP=dist\release\PSX2PSP_Enhanced_v%VER%_Windows_x64.zip"

echo =========================================
echo  Building PSX2PSP Enhanced v%VER%
echo =========================================

:: 1. Compile EXE
echo [1/3] Running PyInstaller...
python -m PyInstaller psx2psp_py/psx2psp_enhanced.spec ^
    --distpath dist --workpath build/pyinstaller --clean --noconfirm
if errorlevel 1 ( echo PyInstaller FAILED & pause & exit /b 1 )

:: 2. Assemble release folder
echo [2/3] Assembling release package...
rmdir /s /q "%REL%" 2>nul
mkdir "%REL%\Files"

:: EXE + docs + scripts
copy /Y "dist\PSX2PSP_Enhanced.exe"  "%REL%\PSX2PSP_Enhanced.exe"
copy /Y "README.md"                   "%REL%\README.md"
copy /Y "CHANGELOG.md"                "%REL%\CHANGELOG.md"
copy /Y "requirements.txt"            "%REL%\requirements.txt"
copy /Y "PSX2PSP_Enhanced.bat"        "%REL%\PSX2PSP_Enhanced.bat"

:: Required runtime tools
copy /Y "at3tool.exe"    "%REL%\at3tool.exe"
copy /Y "lame.exe"       "%REL%\lame.exe"
copy /Y "lame_enc.dll"   "%REL%\lame_enc.dll"
copy /Y "msvcr71.dll"    "%REL%\msvcr71.dll"
copy /Y "PocketISO.exe"  "%REL%\PocketISO.exe"

:: Required data files (ALL of them — do not remove any line)
copy /Y "Files\BASE.PBP"        "%REL%\Files\BASE.PBP"
copy /Y "Files\DATA.PSP"        "%REL%\Files\DATA.PSP"
copy /Y "Files\gameInfo.db"     "%REL%\Files\gameInfo.db"
copy /Y "Files\patches.ini"     "%REL%\Files\patches.ini"
copy /Y "Files\settings.ini"    "%REL%\Files\settings.ini"
copy /Y "Files\no_icon0.png"    "%REL%\Files\no_icon0.png"
copy /Y "Files\back.png"        "%REL%\Files\back.png"
copy /Y "Files\popstation.dll"  "%REL%\Files\popstation.dll"

:: 3. Zip and checksum
echo [3/3] Creating release zip...
powershell -NoProfile -Command ^
    "Compress-Archive -Path '%REL%\*' -DestinationPath '%ZIP%' -Force; ^
     $h = (Get-FileHash '%ZIP%' -Algorithm SHA256).Hash; ^
     \"SHA256: $h  PSX2PSP_Enhanced_v%VER%_Windows_x64.zip\" | Set-Content 'dist\release\SHA256SUMS_v%VER%.txt'; ^
     Write-Host ('ZIP: ' + [math]::Round((Get-Item '%ZIP%').Length/1MB,2) + ' MB'); ^
     Write-Host ('SHA: ' + $h)"

echo.
echo Release package ready: %ZIP%
echo Upload with: gh release create v%VER% "%ZIP%#..." ...
echo.
endlocal
