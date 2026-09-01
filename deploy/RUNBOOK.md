# Deploy runbook — Run8 Track Mapper on a DigitalOcean droplet (P1, single server)

Public host: **https://www.b2fengineering.com**

This is the pick-up-cold, step-by-step for getting the map live for one Run8 server.
It implements the plan in [`../deploy_handoff.md`](../deploy_handoff.md). Multi-tenant
(`/s/<id>/`) is P2 and comes later; nothing here is wasted when we get there.

## The one idea
**Geometry never crosses the wire.** Track geometry is baked once on the Windows box
that has Run8 (`output_generator.py`), then deployed static. The droplet (Linux, no Run8)
only needs the generated **region JSON + `db_railvehicles.db`** to place trains. The
operator's Windows agent pushes **only the world save** (train state, ~200 KB gzipped)
outbound over HTTPS every ~2 min. No inbound ports on the operator's side.

```
 Windows (Run8 server)                         DigitalOcean droplet (Ubuntu)
 ┌────────────────────┐   POST /api/world      ┌──────────────────────────────┐
 │ r8_world_agent.exe │ ───(gzip XML, 443)───► │ Caddy :443  (TLS)            │
 │  watches           │                        │   └─► serve.py 127.0.0.1:8000 │
 │  Auto Save World.xml│                       │         reads output/socal/…  │
 └────────────────────┘                        │         plots trains, /api/…  │
                                               └──────────────────────────────┘
        Browsers ──── https://www.b2fengineering.com ────────────┘
```

## Filesystem layout on the droplet
```
/opt/run8map/
├── app/                    # git clone of the repo (code + config-socal.ini)
│   ├── serve.py, config_parser.py, region_extractor.py, …   (from git)
│   ├── config-socal.ini                                     (from git)
│   ├── output/socal/       # generated: index.html, manifest.json, data/*.json, leaflet/  (copied from Windows)
│   ├── db_railvehicles.db  # (copied from Windows)
│   └── areas_socal.ini     # (copied from Windows; optional)
├── world/                  # writable upload slot; _world_upload.xml lands here
└── run8map.env             # RUN8_UPLOAD_TOKEN (chmod 640, root:run8map)
```
`output/`, `db_railvehicles.db`, and `areas_socal.ini` are **git-ignored**, so `git clone`
does not bring them — they are copied separately in Part D.

---

## Decisions locked in
- **Sequencing:** P1 single server first (this runbook). P2 multi-tenant later.
- **Domain/TLS:** `www.b2fengineering.com`, Caddy auto Let's Encrypt.
  - ⚠️ If `www.b2fengineering.com` (or the apex) already serves another site, decide that
    now — this will take over that hostname. Pick a spare subdomain instead (e.g.
    `map.b2fengineering.com`) by changing the hostname in the `Caddyfile` and the A record.
- **Upload token:** a long random secret shared by the server and the operator's agent.

---

## Part A — DNS
1. In your DNS provider for `b2fengineering.com`, add an **A record**:
   `www` → `<droplet public IPv4>`  (and optionally an **AAAA** for IPv6).
2. Wait for it to resolve (`dig +short www.b2fengineering.com` should show the droplet IP).
   Caddy cannot get a certificate until this points at the droplet.

## Part B — Bake the geometry (Windows + Run8)
Run on your Windows box (you do this; you regenerate on your side):
```
python output_generator.py config-socal.ini --production
```
`--production` hides the "Add Label" authoring button so the public map is view-only.
Output lands in `output/socal/` (index.html, manifest.json, data/*.json, leaflet/).

> Re-run this whenever the track network, colors, labels, or `[trains]`/`[track]` styling
> change. Train **positions** do NOT require a regenerate — they come from the pushed save.

## Part C — Provision the droplet
Create an **Ubuntu LTS** droplet. SSH in as a sudo-capable user, get the repo onto it once
(so you have `deploy/`), then run the bootstrap:
```bash
sudo apt-get update -y && sudo apt-get install -y git
git clone https://github.com/sjstein/Run8TrackMapper.git /tmp/run8map-src
sudo bash /tmp/run8map-src/deploy/bootstrap.sh
```
`bootstrap.sh` installs python3 + git + **Caddy**, creates the `run8map` service account and
`/opt/run8map/{app,world}`, seeds `/opt/run8map/run8map.env` with a **generated token**
(it prints it — copy it for the agent), and opens ufw to **22/80/443 only**.

> Prefer to do it by hand? Every step is echoed in `bootstrap.sh`; follow it top to bottom.

## Part D — Deploy code + data
**Code** (git clone into the app dir):
```bash
sudo -u run8map git clone https://github.com/sjstein/Run8TrackMapper.git /opt/run8map/app
```
**Data** (copy from Windows — these are git-ignored). From the repo root on Windows, in
**PowerShell** or **Git Bash**, using OpenSSH `scp` (swap in your admin user + droplet IP):
```bash
ssh <you>@<droplet> 'mkdir -p /tmp/upload/output'
scp -r output/socal        <you>@<droplet>:/tmp/upload/output/
scp    db_railvehicles.db   <you>@<droplet>:/tmp/upload/
scp    areas_socal.ini      <you>@<droplet>:/tmp/upload/    # optional
```
`output/socal/` is ~142 MB (many small `data/*.json`); `scp -r` is fine, `rsync -az` is
faster if you have it. Then on the droplet, move it into place and fix ownership:
```bash
sudo mkdir -p /opt/run8map/app/output
sudo cp -r /tmp/upload/output/socal   /opt/run8map/app/output/
sudo cp    /tmp/upload/db_railvehicles.db /opt/run8map/app/
sudo cp    /tmp/upload/areas_socal.ini    /opt/run8map/app/ 2>/dev/null || true
sudo rm -rf /tmp/upload
sudo chown -R run8map:run8map /opt/run8map
```

## Part E — Config
No deploy-specific config is needed. `config-socal.ini` (from the clone) runs as-is:
serve.py parses it with **`require_source_files=False`**, so its Windows `C:\Run8Studios\…`
source paths are **not** validated — serve.py reads only `output/socal/data/*.json`. The
systemd unit passes an explicit `--world` slot, which overrides the config's `world_save`
line, so that line is inert on the droplet.

Sanity-check the parse on the droplet:
```bash
cd /opt/run8map/app
python3 -c "from config_parser import parse_config; c=parse_config('config-socal.ini', require_source_files=False); print('OK', c.name, [(r.id,r.route_prefix) for r in c.regions])"
```

## Part F — systemd service
```bash
sudo cp /opt/run8map/app/deploy/run8map.service /etc/systemd/system/run8map.service
# If you did NOT let bootstrap generate the token, edit it now:
sudo nano /opt/run8map/run8map.env          # set RUN8_UPLOAD_TOKEN=...
sudo systemctl daemon-reload
sudo systemctl enable --now run8map
systemctl status run8map --no-pager
```
Confirm it is listening on localhost only and answering:
```bash
curl -s http://127.0.0.1:8000/api/ping        # -> {"ok":true,...,"uploads":true,"world":true}
```
`journalctl -u run8map -f` follows uploads/movement/errors. (On restart, movement resets:
the first save after a (re)start is a baseline with `Moving: 0`; movement needs two saves.)

## Part G — Caddy (TLS + reverse proxy)
```bash
sudo cp /opt/run8map/app/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo journalctl -u caddy -f            # watch it obtain the certificate on first hit
```
With DNS live and 80/443 open, Caddy provisions the Let's Encrypt cert automatically the
first time `https://www.b2fengineering.com` is requested.

## Part H — Operator agent (Windows)
Package the agent once (see `deploy/build-agent.bat`):
```
pip install pyinstaller
deploy\build-agent.bat            # -> dist\r8_world_agent.exe
```
Copy `deploy\agent.ini.example` next to the exe as **`agent.ini`** and set:
- `host_url = https://www.b2fengineering.com`
- `token   = <the RUN8_UPLOAD_TOKEN from the server>`
- `save_file = …\Regions\<REGION>\AutoSaves\Auto Save World.xml`  (the **server's full-world**
  autosave — NOT a client/near-player save)

Test one push, then run it for real:
```
dist\r8_world_agent.exe --once      # single push; prints "uploaded NN KB -> N train(s)"
dist\r8_world_agent.exe             # watch + push every save (~2 min)
```
(No exe? `python r8_world_agent.py --once` works identically.)

## Part I — Verify end to end
1. Browse to **https://www.b2fengineering.com/** — the map loads, padlock is valid.
2. With the agent running (or after `--once`), toggle the **Trains** overlay — vehicles
   appear where they are on the server's world.
3. Let one ~2-min save cycle pass with a train moving; on the next poll (the viewer polls
   `/api/trains` every 3 s) it should move on the map. Movement highlight needs **two**
   saves (first is the baseline).

---

## Troubleshooting
- **`curl /api/ping` fails / service won't start:** `journalctl -u run8map -e`. Most likely a
  path/permission issue under `/opt/run8map` — re-run `sudo chown -R run8map:run8map /opt/run8map`.
  A `ConfigError: Missing files` here means an old serve.py without the tolerant-parse fix —
  make sure the clone is current (`git -C /opt/run8map/app pull`).
- **`output/socal/index.html not found`:** the data copy (Part D) didn't land in
  `/opt/run8map/app/output/socal/`. Check `ls /opt/run8map/app/output/socal`.
- **No cert / TLS errors:** DNS not resolving to the droplet yet, or 80/443 blocked. Check
  `dig +short www.b2fengineering.com` and `sudo ufw status`; watch `journalctl -u caddy -f`.
- **Agent: `HTTP 401 Unauthorized`:** the agent `token` ≠ server `RUN8_UPLOAD_TOKEN`.
- **Agent: `HTTP 403`:** service not started with `--accept-uploads` (it is, in the unit) or
  you hit the wrong host.
- **Trains never appear:** confirm the agent points at the **full-world** autosave and that
  `POST /api/world` logged a train count in `journalctl -u run8map`. The static map (no agent)
  shows only whatever trains were baked into the JSON at generation time.
- **Map shows stale geometry after a regenerate:** re-copy `output/socal/` (Part D). serve.py
  sends `Cache-Control: no-cache`, so a normal browser reload revalidates.

## What changed in the code to enable this
`config_parser.parse_config(config_path, require_source_files=True)` gained the
`require_source_files` flag. Default `True` keeps `output_generator.py` strict (it must find
the real track DBs to bake). `serve.py` calls it with `False`, because at serve time it reads
only the generated `output/<name>/data/*.json` — so a Linux host with no Run8 install runs the
same config whose source paths point at a Windows box. Syntax/structure validation still runs.

## Security notes
- serve.py (stdlib `http.server`) is bound to **127.0.0.1** and only reachable through Caddy.
- The upload slot is `/opt/run8map/world/_world_upload.xml`, **outside** the served directory,
  so the raw world XML is never publicly downloadable (only placed vehicles via `/api/trains`).
- `POST /api/world` is bearer-token gated and size-capped (Caddy 80 MB edge; serve.py 64 MB
  wire / 256 MB decompressed). `--no-authoring` disables all label add/edit/delete APIs.
- ufw allows only 22/80/443. Consider fail2ban and DO's cloud firewall as extra layers.
- Optional rate limiting on `/api/world` needs the `caddy-ratelimit` plugin (not in the stock
  build); the token + size caps are the primary guard for P1.
