# Updating the live deployment

How to push changes to the live map at **https://www.b2fengineering.com/** after P1 is up.

## Mental model (read this first)

The droplet only ever **serves the pre-generated files** in `/opt/run8map/app/output/socal/`
(`index.html`, `manifest.json`, `data/*.json`). It never runs `output_generator.py` — it has no
Run8 source data. `serve.py` on the droplet only does train **placement**; it reads none of your
colors or line widths at runtime (those are baked into `manifest.json` / `index.html` when you
generate on your Windows box).

That splits every change into one of two paths:

| You changed… | Path | Delivered by |
|---|---|---|
| Colors, line widths, area labels, rendering logic, UI, anything visual | **A — regenerate & copy** | file copy of `output/` (NOT git) |
| `serve.py` / placement / `/api/trains` / the API / server behavior | **B — git & restart** | `git pull` on the droplet |

> **Why colors aren't a `git pull`:** `output/` is git-ignored and the droplet can't regenerate it,
> so a `git pull` on the droplet does **not** change what's rendered. The regenerated
> `index.html`/`manifest.json` files have to be copied up. git still carries your `config`/`.py`
> source edit for history — it's just not the delivery mechanism for Path A.

### Where things live
- **Local repo (Windows):** `C:\Users\Josh\dev\Run8TrackMapper`
- **Droplet:** `josh@www.b2fengineering.com`
  - code (clone of `master`): `/opt/run8map/app`
  - served output: `/opt/run8map/app/output/socal/`
  - upload slot: `/opt/run8map/world/_world_upload.xml`
  - token env file: `/opt/run8map/run8map.env` (`RUN8_UPLOAD_TOKEN=…`)
  - service: `run8map.service` (systemd) · reverse proxy: Caddy

---

## Path A — rendering / config changes (colors, line widths, labels, UI)

Most visual tweaks are **config-only** — no code edit. Colors live in `config-socal.ini`
(`[colors]`, `[car_type_colors]`, `[loco_company_colors]`, `[color_presets]`, `[label_types]`);
line widths live in `[track]` (`width` / `min_width` / `full_zoom`) and `[trains]`
(`car_width` / `car_width_m` / `spine_width` / `label_scale_m` / …). Rendering *logic* (how things
draw) is in `html_generator.py`.

**1. Edit locally** — `config-socal.ini` (values) and/or `html_generator.py` (logic).

**2. Regenerate** (Windows, needs Run8 data present):
```bash
python output_generator.py config-socal.ini --production
```
`--production` hides the "Add Label" authoring button on the public map. Output lands in
`output/socal/`.

**3. Preview locally** however you normally do (e.g. open `output/socal/index.html`, or
`python serve.py config-socal.ini --no-authoring` and browse `http://localhost:8000`).

**4. Copy the regenerated output to the droplet.**

**Easiest — one command** (Git Bash, from the repo root):
```bash
deploy/push-output.sh                 # ships output/socal only (the usual case)
deploy/push-output.sh --all           # also ships db_railvehicles.db + areas_socal.ini
deploy/push-output.sh --dry-run       # show what it would do
```
It packs, uploads, extracts into the app dir, fixes ownership, and verifies `/api/ping`
(it uses `ssh -t`, so it'll prompt for your sudo password on the droplet). That's the whole of
step 4. The manual equivalent is below if you ever need it.

<details><summary>By hand</summary>

On your **local** machine (Git Bash):
```bash
cd /c/Users/Josh/dev/Run8TrackMapper
tar czf /tmp/deploy-data.tgz output/socal
scp /tmp/deploy-data.tgz josh@www.b2fengineering.com:/tmp/deploy-data.tgz
```
> Only `output/socal` changes for a rendering/color tweak. Add `db_railvehicles.db` and/or
> `areas_socal.ini` to the `tar` line **only if those changed**:
> `tar czf /tmp/deploy-data.tgz output/socal db_railvehicles.db areas_socal.ini`

Then on the **droplet** (your SSH session):
```bash
sudo tar xzf /tmp/deploy-data.tgz -C /opt/run8map/app
sudo chown -R run8map:run8map /opt/run8map/app
```
</details>

**5. No restart needed.** `serve.py` serves the files fresh and sends `Cache-Control: no-cache`,
so a normal browser reload picks up the change. (If you ever don't see it: hard-reload, Ctrl-F5.)

**6. Commit the source edit for history** (optional but recommended — keeps the config/code record
and lets a future fresh provision reproduce it):
```bash
git add config-socal.ini html_generator.py
git commit -m "Tweak track/train colors and widths"
git push && <merge to master>          # see Path B step 3 for the merge
```
This commit does **not** change the live map — step 4 already did that.

---

## Path B — server code changes (`serve.py`, placement, API)

Use this when you change how the **server** behaves: `serve.py` or its imports
(`region_extractor.py`, `world_parser.py`, `rv_length_db.py`, `config_parser.py`, …), the
`/api/*` endpoints, placement/movement logic, or the `[trains]` runtime knobs `serve.py` reads at
runtime (`deoverlap`, `moving_hysteresis`).

**1. Edit + test locally**, then commit on a working branch:
```bash
git add <files> && git commit -m "…"
git push origin <branch>
```

**2. Merge to `master`** — either open a PR on GitHub and merge it, or fast-forward locally:
```bash
git checkout master && git merge <branch> && git push origin master
git checkout <branch>
```

**3. Update the droplet and restart:**
```bash
cd /opt/run8map/app && sudo -u run8map -H git pull
sudo systemctl restart run8map
systemctl is-active run8map && curl -s http://127.0.0.1:8000/api/ping; echo
```
> The droplet clone currently tracks the branch it was cloned on (`deploy_work`, == `master`).
> To have it follow `master` cleanly: `sudo -u run8map -H git -C /opt/run8map/app checkout master`
> once, then `git pull` pulls `master` thereafter.

> **Note:** `config-socal.ini` is tracked in git, so a Path B `git pull` also updates it on the
> droplet — but that only affects the runtime knobs `serve.py` reads (region prefixes,
> `deoverlap`, `moving_hysteresis`, `output_dir`), **not** colors/widths (those are baked into the
> already-copied `output/`). Changing colors still needs Path A.

---

## Tokens — generating and rotating

The upload token gates `POST /api/world`. The agent must send the same value the server holds.

**Generate a strong token** (anywhere):
```bash
openssl rand -hex 32
```

### Rotate the token (single server — current setup)
On the **droplet**:
```bash
NEWTOKEN=$(openssl rand -hex 32)
printf 'RUN8_UPLOAD_TOKEN=%s\n' "$NEWTOKEN" | sudo tee /opt/run8map/run8map.env >/dev/null
sudo chown root:run8map /opt/run8map/run8map.env && sudo chmod 640 /opt/run8map/run8map.env
sudo systemctl restart run8map
echo ">>> NEW TOKEN (put in the agent; keep secret): $NEWTOKEN"
```
Then update the **agent** (Windows) to the new value — either the `--token` flag or `token =` in
`agent.ini` — and restart it. The old token immediately returns `HTTP 401`.

> Keep the token out of chat/screenshots/commits. `run8map.env` is `chmod 640 root:run8map` so
> only root and the service user can read it; `serve.py` reads it from the environment, so it
> never appears in `ps` / `systemctl status`.

### Adding servers later (P2, multi-tenant — not built yet)
The P2 plan routes each Run8 server under its own URL (`/s/<id>/…`) with its **own** token, kept in
a per-server registry (`server_id → {token-hash, output set, save slot}`; see §7 of
`deploy_handoff.md`). When we build it, the pattern is: `openssl rand -hex 32` **per server**, store
each server's token in its registry entry, and give each operator only their own token. Until P2
exists, there is exactly one token (the one in `run8map.env`) for the single server.

---

## Quick verification (from anywhere)
```bash
curl -s https://www.b2fengineering.com/api/ping                 # {ok:true, uploads:true, world:true}
curl -s https://www.b2fengineering.com/api/trains | python -c "import sys,json;d=json.load(sys.stdin);print(d.get('totals'),'moving',d.get('moving_count'))"
```
Or just load **https://www.b2fengineering.com/**, tick the **Trains** overlay, enable a busy region.

## Rolling back
- **Path A:** keep the previous `deploy-data.tgz` (or re-generate from the prior config) and re-run
  step 4 to restore the old `output/`.
- **Path B:** on the droplet `sudo -u run8map -H git checkout <good-commit>` (or `git revert` +
  push, then pull) and `sudo systemctl restart run8map`.
- A resize/reboot changes nothing here — the service and Caddy are `enable`d and come back on boot.
