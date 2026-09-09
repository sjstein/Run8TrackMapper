============================================================================
 Run8 Map - host your own public track map
============================================================================

This lets people watch the trains on YOUR Run8 server, live, on a map in their
web browser. It runs on the same Windows machine as your r8server. Nothing about
your server is exposed except the public track picture - it is read-only.

You need:
  - Run8 installed on this machine (this tool reads your local route files to
    draw the map).
  - The ability to forward a port on your router (you already do this for Run8).

The bundled map is Southern California (SoCal). If your Run8 is a normal install,
the route paths already match and you do not need to touch config-socal.ini.


----------------------------------------------------------------------------
 WINDOWS SECURITY WARNINGS - THIS IS EXPECTED
----------------------------------------------------------------------------

This program is not code-signed (signing certificates cost money and this is a
free hobby tool), so Windows will warn about it the first time. This is normal
and safe - the warning just means "we don't recognize the publisher", not that
anything is wrong.

  - "Windows protected your PC" (blue box) when you first run it:
        Click "More info", then "Run anyway".

  - Your antivirus flags or quarantines Run8MapHost.exe:
        This is a known false positive with this kind of packaged Python program.
        Allow it / restore it / add an exception for the Run8Map folder.

If you'd rather not, you don't have to take our word for it - ask whoever shared
this with you, or don't run it. But none of the warnings mean the tool is unsafe.


----------------------------------------------------------------------------
 ONE-TIME SETUP
----------------------------------------------------------------------------

1. Unzip this folder anywhere (e.g. C:\Run8Map).

2. Open "Start Map.bat" in Notepad (right-click -> Edit) and set two things:
     - WORLD_SAVE : the full path to your server's world autosave, e.g.
         ...\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml
     - PORT       : the TCP port you will forward (8000 is fine).
   Save the file.

3. Forward that TCP port on your router to this machine.
     - This is a SEPARATE rule from Run8's UDP port. Same router page; choose
       protocol TCP, external+internal port = the PORT you set, pointed at this
       PC's local IP.

4. Double-click "Start Map.bat".
     - The FIRST run builds the map from your route files (this can take a couple
       of minutes). Later runs skip that and start immediately.
     - Windows Firewall may pop up the first time - click "Allow access".
     - When it says "Serving", the map is up. Leave the window open.

5. (Recommended) Get a free dynamic-DNS hostname so people have a stable web
   address even when your home IP changes:
     - Sign up at a free service (e.g. DuckDNS or No-IP), create a hostname, and
       point it at your connection.
     - Share this with viewers:   http://YOUR-HOSTNAME:PORT/
       (e.g. http://myrailroad.duckdns.org:8000/)


----------------------------------------------------------------------------
 GOOD TO KNOW
----------------------------------------------------------------------------

- No padlock (http, not https) is expected and fine. This is a public, read-only
  map with no logins and no sensitive data. (Advanced users with a real domain
  can put a reverse proxy like Caddy in front for https - optional.)

- Keep the window open while you want the map available. To have it start with
  Windows, put a shortcut to "Start Map.bat" in your Startup folder
  (press Win+R, type  shell:startup , press Enter, drop a shortcut there).

- Trains update automatically every couple of minutes as your server autosaves.
  You do not need to do anything.

- If your Run8 is installed somewhere non-standard, edit config-socal.ini and fix
  the region "directory =" paths and "region_dir =" to match your install.

- To rebuild the map after a route change, run once with --regenerate. Easiest:
  hold Shift, right-click in this folder, "Open PowerShell/Terminal window here",
  then:   .\Run8MapHost.exe config-socal.ini --regenerate

- Updating: if a newer version is published you'll see a one-line notice in the
  window when the map starts. Download the new folder and copy your edited
  "Start Map.bat" (and config, if you changed it) into it.


----------------------------------------------------------------------------
 TROUBLESHOOTING
----------------------------------------------------------------------------

- The window opens and closes instantly / shows an error:
    Read the message. Usually the WORLD_SAVE path is wrong, or config-socal.ini
    points at a Run8 folder that doesn't exist on this machine.

- The map loads for me locally but friends can't reach it:
    Your port forward or Windows Firewall is blocking it. Test your own public
    address from a phone on cellular data (not your home wifi):
      http://YOUR-HOSTNAME:PORT/

- Trains don't appear or don't move:
    Check WORLD_SAVE points at the SERVER's full-world autosave (not a client
    save). Trains need at least one autosave to appear, two to show movement.

- "Port already in use":
    Pick a different PORT in Start Map.bat (and forward that one instead).
