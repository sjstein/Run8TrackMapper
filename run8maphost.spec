# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Run8 Map self-host bundle (Run8MapHost).

Build from the repo root, inside the project venv:

    venv\\Scripts\\activate
    pip install pyinstaller
    pyinstaller run8maphost.spec --noconfirm

Output: dist/Run8MapHost/  (onedir)
  Run8MapHost.exe         - the launcher (host_map.py)
  _internal/              - Python runtime + all local modules + bundled data:
                            vendor/leaflet, db_railvehicles.db

`build_bundle.ps1` wraps this and then stages the operator-editable files
(config-socal.ini, areas_socal.ini, README, Start Map.bat) beside the .exe. Ship
the whole dist/Run8MapHost/ folder (zipped) to an operator.

Notes
- The runtime import tree is stdlib + local modules only (no folium/numpy/etc.),
  so no hiddenimports are needed and heavy libs are excluded to keep it lean.
- vendor/leaflet is placed at the bundle root so output_generator.copy_leaflet_assets
  finds it under sys._MEIPASS (see its frozen branch). db_railvehicles.db likewise, so
  host_map._apply_bundled_assets can point the config at it.
"""

# App-owned data assets, shipped at the bundle root (dest '.'/matching subdir).
datas = [
    ('vendor/leaflet', 'vendor/leaflet'),
    ('db_railvehicles.db', '.'),
    # Default config + area labels shipped as TEMPLATES inside the exe (not as loose
    # files). host_map seeds them beside the exe on first run if absent, so a later
    # release never overwrites an operator's edited copies.
    ('config-socal.ini', '.'),
    ('areas_socal.ini', '.'),
]

# Third-party libs that may sit in the venv but are NOT on the runtime path; keep
# them out so the bundle stays small (belt-and-suspenders - Analysis won't pull them
# unless something imports them).
excludes = [
    'folium', 'jinja2', 'branca', 'numpy', 'pandas', 'matplotlib',
    'tkinter', 'PIL', 'pytest', 'IPython',
]

a = Analysis(
    ['host_map.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Run8MapHost',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX often trips Windows AV; keep off for a shippable exe
    console=True,         # the operator watches the log / share URL in this window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Run8MapHost',
)
