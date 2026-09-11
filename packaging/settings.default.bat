@echo off
REM ============================================================================
REM  Run8 Map - YOUR settings.  Edit the values below, then run "Start Map.bat".
REM
REM  This is the TEMPLATE (settings.default.bat). On first run, Start Map.bat
REM  copies it to "settings.bat" - edit THAT one. Your settings.bat is yours and
REM  a future release will NOT overwrite it (only this template ships in updates).
REM ============================================================================

REM  WORLD_SAVE : full path to YOUR Run8 server's world autosave (required for
REM               live trains). The server's full-world save, e.g.:
REM               ...\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml
set "WORLD_SAVE=C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml"

REM  PORT       : the TCP port you forward on your router (or expose via a tunnel).
REM               8000 is fine unless it's already in use.
set "PORT=8000"

REM  CONFIG_FILE: the map config beside the program. Leave as-is for SoCal; it is
REM               created automatically on first run and is yours to edit.
set "CONFIG_FILE=config-socal.ini"

REM  RUN8_EDIT_PASSWORD (OPTIONAL): leave BLANK for a normal read-only public map.
REM               Set a shared password to let your staff edit area labels on the
REM               live map (they type the word "edit" on the map, then enter it).
REM               *** If you set this, do NOT expose the map on a plain http port ***
REM               - serve it over https via a free tunnel (Tailscale Funnel). The
REM               password itself is never sent, but an unlocked edit session could
REM               otherwise be hijacked. See README.txt "ENABLE LABEL EDITING".
set "RUN8_EDIT_PASSWORD="
