@echo off
REM ============================================================================
REM  Run8 Map - start your track map.  Double-click this file.
REM
REM  Your settings live in "settings.bat" (who can reach the map, world-save path,
REM  port, edit password). It is created from the template on first run and is NOT
REM  overwritten by updates - so to update, just unzip the new release over this
REM  folder.
REM ============================================================================

REM Run from this folder so config, output\ and settings.bat are found beside the .exe.
cd /d "%~dp0"

REM First run: materialise your editable settings.bat from the shipped template.
REM NOTE: inverted guard + goto, NOT an "if not exist (...)" block. Inside a
REM parenthesized block cmd.exe reads any literal ")" in an echo line as the block
REM end and fails with ". was unexpected at this time.", so settings.bat is never
REM created. Keep the echo lines below free of ( ) & | < > all the same.
if exist "settings.bat" goto :settings_ready
copy /y "settings.default.bat" "settings.bat" >nul
echo.
echo  * First run: created "settings.bat" from the template. With the defaults the
echo    map is LOCAL-ONLY: you open it in a browser on this PC and nothing is shared.
echo    Edit settings.bat later to plot your trains [WORLD_SAVE] or to share the map
echo    with other people [HOST]. settings.bat is yours - updates won't touch it.
echo.
:settings_ready

REM Load your settings (defines HOST, WORLD_SAVE, PORT, CONFIG_FILE, RUN8_EDIT_PASSWORD).
set "HOST="
call "settings.bat"

REM A settings.bat created by an older release has no HOST line. Those installs were
REM shared maps (the old launcher always listened on every interface), so keep them
REM shared rather than silently taking a public map offline on update. New installs
REM get HOST=127.0.0.1 (local-only) from the template.
if not defined HOST set "HOST=0.0.0.0"

REM WORLD_SAVE is optional: blank = track map only, no trains.
set "WORLD_ARG="
if defined WORLD_SAVE set WORLD_ARG=--world "%WORLD_SAVE%"

Run8MapHost.exe "%CONFIG_FILE%" --host %HOST% --port %PORT% %WORLD_ARG%

echo.
echo ============================================================================
echo  The map server has stopped. Read any messages above for the reason.
echo ============================================================================
pause
