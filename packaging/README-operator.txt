============================================================================
 Run8 Map - host your own public track map
============================================================================

This lets people watch the trains on YOUR Run8 server, live, on a map in their
web browser. It runs on the same Windows machine as your r8server. Nothing about
your server is exposed except the public track picture, which is read-only by
default. (You can optionally let trusted staff edit the map's text labels behind
a password - see "ENABLE LABEL EDITING" below.)

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

2. Double-click "Start Map.bat" once. It creates your personal "settings.bat"
   (from the shipped template) and then starts. Close it, then open "settings.bat"
   in Notepad (right-click -> Edit) and set:
     - WORLD_SAVE : the full path to your server's world autosave, e.g.
         ...\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml
     - PORT       : the TCP port you will forward (8000 is fine).
   Save it. (settings.bat is YOURS - a future update will not overwrite it. Same
   goes for config-socal.ini / areas_socal.ini, which are created beside the
   program on first run.)

3. Forward that TCP port on your router to this machine.
     - This is a SEPARATE rule from Run8's UDP port. Same router page; choose
       protocol TCP, external+internal port = the PORT you set, pointed at this
       PC's local IP.

4. Double-click "Start Map.bat" again.
     - The FIRST real run builds the map from your route files (this can take a
       couple of minutes). Later runs skip that and start immediately.
     - Windows Firewall may pop up the first time - click "Allow access".
     - When it says "Serving", the map is up. Leave the window open.

5. (Recommended) Get a free dynamic-DNS hostname so people have a stable web
   address even when your home IP changes:
     - Sign up at a free service (e.g. DuckDNS or No-IP), create a hostname, and
       point it at your connection.
     - Share this with viewers:   http://YOUR-HOSTNAME:PORT/
       (e.g. http://myrailroad.duckdns.org:8000/)


----------------------------------------------------------------------------
 ENABLE LABEL EDITING (OPTIONAL, ADVANCED)
----------------------------------------------------------------------------

By default the map is READ-ONLY: anyone can view it, nobody can change it. You
can optionally let your trusted staff add and edit the text labels (yard names,
control points, track labels...) live on the map, protected by a shared password.

  *** READ THIS FIRST - SECURITY ***
  The edit password itself is NEVER sent over the network - the map proves you
  know it without transmitting it - so the password cannot be sniffed or stolen,
  even over a plain "http://" connection.

  What a plain "http://" address DOES expose is the temporary edit SESSION: after
  a staff member unlocks, someone watching the traffic could hijack that active
  session and add / change / delete labels until it expires (they still never
  learn the password). These are only map labels (reversible, auto-backed-up to a
  .bak file), so some hosts accept this - but serving over https removes the risk.

    - Read-only map   -> a plain port forward (http) is completely fine.
    - Editing enabled -> https via a free tunnel is STRONGLY RECOMMENDED (below).
                         A plain port forward still works, but only do it if you
                         accept the session-hijack risk described above.

Turn editing on:

  1. Open "settings.bat" in Notepad and set a password on the RUN8_EDIT_PASSWORD
     line, e.g.:
         set "RUN8_EDIT_PASSWORD=some-long-shared-passphrase"
     Pick something long; share it only with staff who should edit. Leaving it
     blank keeps the map read-only.

  2. Strongly recommended: expose the map over https with a tunnel (see the next
     section) rather than a raw port forward, so an active edit session can't be
     hijacked. Start the map, then start the tunnel. (A plain port forward works
     too, if you accept that risk.)

  3. Staff editing: on the map, TYPE the word  edit  (just type it, there is no
     button), enter the password, and the "Add Label" tools appear. Click
     "Leave editing" (or reload) to lock again. There is nothing on screen that
     tells a normal viewer editing exists.

  Change the password later by editing Start Map.bat and restarting the map.


----------------------------------------------------------------------------
 EXPOSING THE MAP SECURELY (https) - STRONGLY RECOMMENDED IF EDITING IS ON
----------------------------------------------------------------------------

These give your map a proper "https://" address so the whole edit session is
encrypted (the password is already safe either way - see above). A tunnel also
means you do NOT port-forward anything (nothing on this PC is opened to the
internet directly).

RECOMMENDED - Tailscale Funnel (free, https, no port forwarding, stable address):

  1. Install Tailscale (https://tailscale.com/download), sign in, and in the
     admin console enable HTTPS certificates and Funnel for this machine
     (Settings -> Features; the Funnel node attribute).
  2. Start your map as usual (Start Map.bat). Leave PORT as 8000.
  3. In a terminal on this PC, publish that port:
         tailscale funnel 8000
     It prints a public https address like
         https://your-pc.your-tailnet.ts.net/
     Share THAT address with viewers. (Add  --bg  to run it in the background:
     tailscale funnel --bg 8000 .)
  4. You do NOT need a router port forward for the map when using Funnel. If you
     previously forwarded the map's port, you can remove that rule.

ALTERNATIVE - Caddy (if you already own a domain name):

  1. Point your domain (e.g. map.yourrailroad.com) at your connection and forward
     TCP ports 80 and 443 to this PC.
  2. Install Caddy (https://caddyserver.com) and create a "Caddyfile" containing:
         map.yourrailroad.com {
             reverse_proxy localhost:8000
         }
  3. Run  caddy run  . Caddy fetches a free HTTPS certificate automatically and
     serves your map at https://map.yourrailroad.com/ .


----------------------------------------------------------------------------
 GOOD TO KNOW
----------------------------------------------------------------------------

- No padlock (http, not https) is expected and fine FOR A READ-ONLY MAP - it has
  no logins and no sensitive data. (If you enable label editing, that no longer
  applies: you must serve it over https via a tunnel - see the editing section
  above.)

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
  window when the map starts. Download the new zip from the Releases page:
    https://github.com/sjstein/Run8TrackMapper/releases
  and unzip it OVER this folder (overwrite when asked). Your own files are safe:
  settings.bat, config-socal.ini and areas_socal.ini are NOT in the zip, so your
  edits and labels are kept - only the program, README, and the templates update.


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
