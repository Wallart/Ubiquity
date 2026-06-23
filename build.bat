@echo off
REM Build Ubiquity for Windows.
REM
REM Prerequisites:
REM   pip install pyinstaller
REM   Inno Setup 6 (https://jrsoftware.org/isinfo.php) for the installer step
REM
REM Usage:
REM   build.bat             -- exe only  ->  dist\ubiquity.exe
REM   build.bat installer   -- exe + no-admin installer -> dist\UbiquitySetup.exe

pyinstaller ubiquity.spec --clean
if errorlevel 1 goto :error
echo [OK] dist\ubiquity.exe

if /i "%1"=="installer" (
    where iscc >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] iscc not found. Install Inno Setup and add it to PATH.
        goto :error
    )
    iscc installer\windows\setup.iss
    if errorlevel 1 goto :error
    echo [OK] dist\UbiquitySetup.exe
)

goto :eof
:error
echo Build failed.
exit /b 1
