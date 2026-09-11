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

## Publish (GitHub Releases)

The repo is public, so **GitHub Releases** host the bundle and drive the update check -
no droplet / static host needed.

1. Create a release for this version. Either the web UI (Releases -> "Draft a new
   release") or the CLI:
   ```bash
   gh release create v<version> \
       "dist/Run8MapHost-<version>.zip" \
       "dist/Run8MapHost-<version>.zip.sha256" \
       --title "Run8MapHost <version>" --notes "..."
   ```
   - **Tag** it `v<version>` (or `<version>` - the launcher strips a leading `v`).
     Keep it in step with `version.py`.
   - Attach the **.zip** (and the `.sha256`) as release assets - the .zip is what
     `host_map.check_for_update` hands operators as the download link.
   - Leave "Set as the latest release" checked (the update check reads
     `/releases/latest`, which ignores drafts and pre-releases).
2. That's it. Operators running an older bundle see a one-line "newer version
   available" notice at startup, pointing at the new release's .zip (best-effort;
   offline boxes just skip it). No `latest.json`, no server to update.

Operators download from the repo's **Releases** page:
`https://github.com/sjstein/Run8TrackMapper/releases`.

## Notes / not-yet-done

- **Unsigned exe:** operators hit Windows SmartScreen + occasional AV false positives.
  Documented in `README-operator.txt` ("Run anyway"). Code signing (~$100+/yr cert)
  would remove it — only worth it if the friction proves to be a real barrier.
- **Update preserves operator files (done):** the operator's editable files are NOT
  shipped as loose files in the zip, so unzipping a new release over the folder can't
  clobber them:
  - `config-socal.ini` / `areas_socal.ini` ride inside the exe as templates
    (`run8maphost.spec` `datas`); `host_map._seed_from_template` writes them beside the
    exe on first run (with a console warning) if missing, then leaves them alone.
  - Operator settings (world-save path, port, edit password) live in `settings.bat`,
    which `Start Map.bat` seeds from the shipped `settings.default.bat` template on first
    run. The zip ships only the template, never `settings.bat`.
  So the staged files in the zip are just `README.txt`, `Start Map.bat`, and
  `settings.default.bat` (plus the exe + `_internal/`).
- **Zip step** reads the whole file into memory for the sha256 (fine at ~15 MB).
