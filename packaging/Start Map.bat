@echo off
REM ============================================================================
REM  Run8 Map - start your public track map.  Double-click this file.
REM
REM  Your settings live in "settings.bat" (world-save path, port, edit password).
REM  It is created from the template on first run and is NOT overwritten by
REM  updates - so to update, just unzip the new release over this folder.
REM ============================================================================

REM Run from this folder so config, output\ and settings.bat are found beside the .exe.
cd /d "%~dp0"

REM First run: materialise your editable settings.bat from the shipped template.
if not exist "settings.bat" (
    copy /y "settings.default.bat" "settings.bat" >nul
    echo.
    echo  * First run: created "settings.bat" from the template. Open it to set your
    echo    WORLD_SAVE / PORT (and an edit password if you want live label editing).
    echo    Using the defaults for now. settings.bat is yours - updates won't touch it.
    echo.
)

REM Load your settings (defines WORLD_SAVE, PORT, CONFIG_FILE, RUN8_EDIT_PASSWORD).
call "settings.bat"

Run8MapHost.exe "%CONFIG_FILE%" --port %PORT% --world "%WORLD_SAVE%"

echo.
echo ============================================================================
echo  The map server has stopped. Read any messages above for the reason.
echo ============================================================================
pause
