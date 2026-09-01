@echo off
REM ---------------------------------------------------------------------------
REM Build the Run8 world-save uploader agent into a single Windows .exe so a
REM non-technical operator just edits agent.ini and double-clicks.
REM
REM One-time setup:  pip install pyinstaller
REM Run this from the repo root:  deploy\build-agent.bat
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0.."

where pyinstaller >nul 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Install it first:
    echo     pip install pyinstaller
    exit /b 1
)

pyinstaller --onefile --console --name r8_world_agent r8_world_agent.py
if errorlevel 1 exit /b 1

echo.
echo Built: dist\r8_world_agent.exe
echo.
echo Next: copy deploy\agent.ini.example next to the exe as agent.ini, edit the
echo three values (host_url / token / save_file), then run:
echo     dist\r8_world_agent.exe --once
endlocal
