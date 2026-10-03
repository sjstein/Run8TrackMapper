============================================================================
 Run8 Map - your Run8 route, and its trains, on a map
============================================================================

This draws your Run8 route on a real map in your web browser, with the trains
from a world save plotted on the track. There are two ways to use it:

  PART 1 - JUST VIEW YOUR ROUTE on this PC. This is the default. Nothing is
           shared with anyone and there is nothing to set up.

  PART 2 - SHARE THE MAP so other people can watch the trains on your Run8
           server live. This is optional and takes a few extra steps.

You need Run8 installed on this machine (this tool reads your local route files
to draw the map). Nothing else.

The bundled map is Southern California (SoCal). If your Run8 is a normal install,
the route paths already match and you do not need to touch config-socal.ini.


----------------------------------------------------------------------------
 PART 1 - JUST WANT TO VIEW YOUR ROUTE?
----------------------------------------------------------------------------

1. Unzip this folder anywhere (e.g. C:\Run8Map).

2. Double-click "Start Map.bat".
     - Windows will warn about an unrecognized program the first time. That is
       expected - see WINDOWS SECURITY WARNINGS below.
     - The FIRST run builds the map from your route files (this can take a
       couple of minutes). Later runs skip that and start immediately.

3. When the window says "Serving", open this address in your web browser:

       http://localhost:8000/

   That's it. Leave the window open while you use the map; close it when done.

The map is LOCAL-ONLY: only a browser on this PC can open it. Nothing is exposed
to your network or the internet, and you do not need to forward a port, allow
anything through the firewall, or set a password.

Optional extras (both set in "settings.bat", which Start Map.bat creates beside
it on the first run - open it in Notepad with right-click -> Edit):

  - See your trains. WORLD_SAVE is the Run8 world save (.xml) whose trains are
    drawn on the track. Point it at a world you saved, or at your autosave, e.g.
        ...\Content\V3Routes\Regions\SouthernCA\AutoSaves\Auto Save World.xml
    The map re-reads the file whenever Run8 saves it again, so with autosave on
    the trains move by themselves. Leave it blank for the track map only.

  - Add your own labels. Set RUN8_EDIT_PASSWORD to anything, restart, then TYPE
    the word  edit  on the map and enter it. See ENABLE LABEL EDITING below (the
    security notes there are about shared maps and do not apply to a local one).

If your Run8 is installed somewhere non-standard, edit config-socal.ini and fix
the region "directory =" paths and "region_dir =" to match your install.


----------------------------------------------------------------------------
 WINDOWS SECURITY WARNINGS - THIS IS EXPECTED
----------------------------------------------------------------------------

This program is not code-signed (signing certificates cost money and this is a
free hobby tool), so Windows will warn about it the first time. This is normal
and safe - the warnings just mean "we don't recognize the publisher", not that
anything is wrong. On the very first run you will see these prompts, in this
order - you must click through them or the map won't start:

  1. "Open File - Security Warning" (grey box) when you double-click
     "Start Map.bat":
        Click "Run".  (If you tick "Always ask before opening this file" off,
        you won't see it again.)

  2. "Windows protected your PC" (blue box) - this is SmartScreen, shown when the
     launcher starts Run8MapHost.exe:
        Click "More info", then "Run anyway".  (The default button is
        "Don't run" - if you click that, the exe never runs and no map is built.)

  3. ONLY IF YOU ARE SHARING THE MAP (Part 2, HOST=0.0.0.0):
     "Windows Security Alert" / Windows Firewall - asks whether to allow network
     access:
        Click "Allow access".  Without it, other people cannot reach the map.
     A local-only map (the default) does not trigger this prompt.

  Also: your antivirus may flag or quarantine Run8MapHost.exe. This is a known
  false positive with this kind of packaged Python program - allow it / restore
  it / add an exception for the Run8Map folder.

If you'd rather not, you don't have to take our word for it - ask whoever shared
this with you, or don't run it. But none of the warnings mean the tool is unsafe.


----------------------------------------------------------------------------
 PART 2 - SHARING YOUR MAP PUBLICLY (server hosts)
----------------------------------------------------------------------------

Skip this whole part if you only view the map yourself.

This lets people watch the trains on YOUR Run8 server, live, in their browser.
Run it on the same Windows machine as your r8server. Nothing about your server
is exposed except the public track picture, which is read-only by default. (You
can optionally let trusted staff edit the map's text labels behind a password -
see "ENABLE LABEL EDITING" below.)

You also need the ability to forward a port on your router (you already do this
for Run8) - or use a tunnel instead, see EXPOSING THE MAP SECURELY.

1. Do Part 1 first, so the map is built and works at http://localhost:8000/ .
   Then close the window.

2. Open "settings.bat" in Notepad (right-click -> Edit) and set:
     - HOST       : 0.0.0.0   (this is what makes the map reachable from other
                    computers; the default 127.0.0.1 keeps it local-only).
     - WORLD_SAVE : the full path to your SERVER's world autosave, e.g.
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
     - Windows Firewall pops up the first time the map is shared - click
       "Allow access" (required; see WINDOWS SECURITY WARNINGS above).
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

By default the map is READ-ONLY: anyone who can open it can view it, nobody can
change it. You can optionally add and edit the text labels (yard names, control
points, track labels...) live on the map, protected by a password - for yourself
on a local map, or for your trusted staff on a shared one.

  *** SHARED MAPS: READ THIS FIRST - SECURITY ***
  (None of this applies to a local-only map - nobody else can reach it.)

  The edit password itself is NEVER sent over the network - the map proves you
  know it without transmitting it - so the password cannot be sniffed or stolen,
  even over a plain "http://" connection.

  What a plain "http://" address DOES expose is the temporary edit SESSION: after
  a staff member unlocks, someone watching the traffic could hijack that active
  session and add / change / delete labels until it expires (they still never
  learn the password). These are only map labels (reversible, auto-backed-up to a
  .bak file), so some hosts accept this - but serving over https removes the risk.

    - Local-only map  -> no risk; set any password.
    - Read-only map   -> a plain port forward (http) is completely fine.
    - Editing enabled -> https via a free tunnel is STRONGLY RECOMMENDED (below).
                         A plain port forward still works, but only do it if you
                         accept the session-hijack risk described above.

Turn editing on:

  1. Open "settings.bat" in Notepad and set a password on the RUN8_EDIT_PASSWORD
     line, e.g.:
         set "RUN8_EDIT_PASSWORD=some-long-shared-passphrase"
     On a shared map pick something long, and share it only with staff who should
     edit. Leaving it blank keeps the map read-only.

  2. Shared map, strongly recommended: expose it over https with a tunnel (see
     the next section) rather than a raw port forward, so an active edit session
     can't be hijacked. Start the map, then start the tunnel. (A plain port
     forward works too, if you accept that risk.)

  3. Editing: on the map, TYPE the word  edit  (just type it, there is no
     button), enter the password, and the "Add Label" tools appear. Click
     "Leave editing" (or reload) to lock again. There is nothing on screen that
     tells a normal viewer editing exists.

  Change the password later by editing settings.bat and restarting the map.


----------------------------------------------------------------------------
 EXPOSING THE MAP SECURELY (https) - STRONGLY RECOMMENDED IF EDITING IS ON
----------------------------------------------------------------------------

These give a shared map a proper "https://" address so the whole edit session is
encrypted (the password is already safe either way - see above). A tunnel also
means you do NOT port-forward anything (nothing on this PC is opened to the
internet directly).

With either of these, leave HOST at 127.0.0.1 in settings.bat: the tunnel runs
on this PC and connects to the map locally, so the map itself never has to
listen on your network.

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
             reverse_proxy 127.0.0.1:8000
         }
  3. Run  caddy run  . Caddy fetches a free HTTPS certificate automatically and
     serves your map at https://map.yourrailroad.com/ .


----------------------------------------------------------------------------
 GOOD TO KNOW
----------------------------------------------------------------------------

- No padlock (http, not https) is expected and fine FOR A LOCAL OR READ-ONLY MAP
  - it has no logins and no sensitive data. (If you enable label editing on a
  SHARED map, that no longer applies: serve it over https via a tunnel - see the
  editing section above.)

- Keep the window open while you want the map available. To have it start with
  Windows, put a shortcut to "Start Map.bat" in your Startup folder
  (press Win+R, type  shell:startup , press Enter, drop a shortcut there).

- Trains update automatically each time Run8 saves the world again (every couple
  of minutes with autosave on). You do not need to do anything.

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
  The first start after an update rebuilds the map (a couple of minutes, like the
  very first run) so you get the new version's map page; later starts are quick.

- Updating from an older version: your existing settings.bat has no HOST line.
  The map then stays SHARED (0.0.0.0), exactly as it behaved before, so a public
  map keeps working after the update. To make it local-only, add this line to
  settings.bat:      set "HOST=127.0.0.1"


----------------------------------------------------------------------------
 TROUBLESHOOTING
----------------------------------------------------------------------------

- The window opens and closes instantly / shows an error:
    Read the message. Usually config-socal.ini points at a Run8 folder that
    doesn't exist on this machine.

- The map loads for me locally but friends can't reach it:
    First check HOST is 0.0.0.0 in settings.bat (the window says "Sharing : off"
    at startup when the map is local-only). If it is, your port forward or
    Windows Firewall is blocking it. Test your own public address from a phone on
    cellular data (not your home wifi):
      http://YOUR-HOSTNAME:PORT/

- Trains don't appear or don't move:
    Check WORLD_SAVE in settings.bat points at a world save that exists (the
    window says "not found yet" at startup when it doesn't). On a server, it must
    be the SERVER's full-world autosave, not a client save. Trains need at least
    one save to appear, two to show movement.

- "Port already in use":
    Pick a different PORT in settings.bat (and, if you share the map, forward
    that one instead).
