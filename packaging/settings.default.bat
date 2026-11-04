@echo off
REM ============================================================================
REM  Run8 Map - YOUR settings.  Edit the values below, then run "Start Map.bat".
REM
REM  This is the TEMPLATE (settings.default.bat). On first run, Start Map.bat
REM  copies it to "settings.bat" - edit THAT one. Your settings.bat is yours and
REM  a future release will NOT overwrite it (only this template ships in updates).
REM
REM  Just want to look at your route on this PC? You do not have to change
REM  anything here. Run "Start Map.bat" and open http://localhost:8000/
REM ============================================================================

REM  HOST       : who can reach the map.
REM                 127.0.0.1 = LOCAL-ONLY (default). Only a browser on this PC can
REM                             open it. Nothing is shared, no firewall prompt.
REM                 0.0.0.0   = SHARED. Other computers can reach it (your LAN, and
REM                             the internet once you forward PORT on your router).
REM               Sharing through a tunnel (Tailscale Funnel / Caddy)? Leave this at
REM               127.0.0.1 - the tunnel connects locally. See README.txt.
set "HOST=127.0.0.1"

REM  WORLD_SAVE (OPTIONAL): a Run8 world save (.xml) whose trains are plotted on
REM               the map. The track map works without it - leave it BLANK
REM               (set "WORLD_SAVE=") for track only. It does not have to exist yet;
REM               the map picks it up as soon as Run8 writes it, and re-reads it
REM               every time it is saved again, so with autosave on the trains move.
REM                 - Single player: your own saved world / autosave .xml.
REM                 - Server host:   the SERVER's full-world autosave, e.g.
REM               ...\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml
set "WORLD_SAVE=C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml"

REM  RUN8_TELEMETRY_URL (OPTIONAL): leave BLANK to update trains only when the
REM               world save changes. Set this to a Run8 telemetry bridge URL for
REM               near-real-time positions, simulation time, and switch state.
REM               A bridge on this same PC normally uses http://127.0.0.1:40872.
REM               WORLD_SAVE remains the cold-start and outage fallback when both
REM               settings are present. Never expose the bridge itself publicly.
set "RUN8_TELEMETRY_URL="

REM  RUN8_TELEMETRY_TOKEN_FILE (OPTIONAL): full path to a text file containing
REM               the bridge bearer token. Leave BLANK when bridge authentication
REM               is disabled. A configured missing or empty file stops startup.
set "RUN8_TELEMETRY_TOKEN_FILE="

REM  RUN8_TRAIN_POLL_MS (OPTIONAL): how often browsers request a shared live train
REM               snapshot, in milliseconds. 5000 (5 seconds) is recommended for
REM               public maps. Valid range: 1000 through 60000. The server builds
REM               and compresses at most one snapshot per interval for all viewers.
set "RUN8_TRAIN_POLL_MS=5000"

REM  PORT       : the port in the map's address (http://localhost:PORT/). 8000 is
REM               fine unless something else is already using it. When SHARING,
REM               this is the TCP port you forward on your router (or point a
REM               tunnel at).
set "PORT=8000"

REM  CONFIG_FILE: the map config beside the program. Leave as-is for SoCal; it is
REM               created automatically on first run and is yours to edit.
set "CONFIG_FILE=config-socal.ini"

REM  RUN8_EDIT_PASSWORD (OPTIONAL): leave BLANK for a read-only map.
REM               Set a password to add / edit the map's text labels on the live map
REM               (type the word "edit" on the map, then enter it).
REM               Local-only map: any password will do - nobody else can reach it.
REM               *** SHARED map: do NOT expose it on a plain http port with this
REM               set *** - serve it over https via a free tunnel (Tailscale Funnel).
REM               The password itself is never sent, but an unlocked edit session
REM               could otherwise be hijacked. See README.txt "ENABLE LABEL EDITING".
set "RUN8_EDIT_PASSWORD="
