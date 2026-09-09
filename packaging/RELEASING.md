# Releasing the Run8 Map self-host bundle

Maintainer notes for cutting and publishing a bundle release. (This file is NOT
shipped to operators - only `README-operator.txt` and `Start Map.bat` are staged
into the dist.)

## Cut a release

1. Bump the version in [`version.py`](../version.py) (`__version__`).
2. Build (from the repo root, on Windows):
   ```bash
   ./build_bundle.sh          # bash / Git Bash
   # or:  .\build_bundle.ps1  # PowerShell
   ```
   This produces, under `dist/`:
   - `Run8MapHost/` — the unpacked bundle (exe + `_internal/` + operator files)
   - `Run8MapHost-<version>.zip` — the release artifact to hand out
   - `Run8MapHost-<version>.zip.sha256` — its checksum
3. Smoke-test the exe once (see the command the build prints) before publishing.

## Publish (download + update-check hosting)

Everything is hosted on the existing droplet (`www.b2fengineering.com`) — the repo
is private, so GitHub Release assets aren't publicly downloadable.

1. Upload the zip (and `.sha256`) to the droplet under a `run8map/` web directory,
   e.g. `…/run8map/Run8MapHost-<version>.zip`.
2. Write/replace `…/run8map/latest.json` so the in-app update check works. Its shape
   (see `host_map.check_for_update` / `DEFAULT_UPDATE_URL`):
   ```json
   {
     "version": "0.2.0",
     "url": "https://www.b2fengineering.com/run8map/Run8MapHost-0.2.0.zip"
   }
   ```
   Operators running an older bundle then see a one-line "newer version available"
   notice at startup (best-effort; offline boxes just skip it).
3. Announce the download link wherever operators gather (Discord/forum).

### Caddy: serve the download directory

Add a static-file route on the droplet so `run8map/*` is downloadable (adjust the
site block / root to match the existing Caddyfile):

```
www.b2fengineering.com {
    # ... existing map/reverse-proxy config ...

    handle_path /run8map/* {
        root * /var/www/run8map
        file_server browse
    }
}
```

Then place the zip + `latest.json` in `/var/www/run8map/`.

## Notes / not-yet-done

- **Unsigned exe:** operators hit Windows SmartScreen + occasional AV false positives.
  Documented in `README-operator.txt` ("Run anyway"). Code signing (~$100+/yr cert)
  would remove it — only worth it if the friction proves to be a real barrier.
- **Update preserves settings:** updating currently means the operator re-copies their
  edited `Start Map.bat` into the new folder. A future refinement is to keep operator
  settings in a separate file the bundle reads, so an update can replace everything else.
- **Zip step** reads the whole file into memory for the sha256 (fine at ~15 MB).
