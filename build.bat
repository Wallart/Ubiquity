@echo off
REM Build the Windows executable.
REM Run once: pip install pyinstaller
pyinstaller ubiquity.spec --clean
echo Binary: dist\ubiquity.exe
