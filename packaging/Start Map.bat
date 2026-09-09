@echo off
REM ============================================================================
REM  Run8 Map - start your public track map
REM  Double-click this file to build (first run) and serve your map.
REM  Edit the SETTINGS block below, save, then double-click.
REM ============================================================================

REM Run from this folder so the config and output\ are found next to the .exe.
cd /d "%~dp0"

REM ======================== SETTINGS - EDIT THESE =============================

REM  WORLD_SAVE : full path to YOUR Run8 server's world autosave (required).
REM               This is the server's full-world save, e.g.:
REM               ...\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml
set "WORLD_SAVE=C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml"

REM  PORT       : the TCP port you forwarded on your router for the map
REM               (a SEPARATE rule from Run8's UDP port). 8000 is fine unless
REM               it's already in use.
set "PORT=8000"

REM  CONFIG_FILE: the map config in this folder. Most SoCal operators leave this
REM               as-is. Only change it if your Run8 is installed in a
REM               non-standard location (edit the paths inside config-socal.ini),
REM               or point it at a different config if you host a different route.
set "CONFIG_FILE=config-socal.ini"

REM ===========================================================================

Run8MapHost.exe "%CONFIG_FILE%" --port %PORT% --world "%WORLD_SAVE%"

echo.
echo ============================================================================
echo  The map server has stopped. Read any messages above for the reason.
echo ============================================================================
pause
