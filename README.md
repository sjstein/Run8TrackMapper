# Run8 Track Mapper

Visualize Run8 Train Simulator track networks on real-world maps. It parses the
binary track database (`.r8`) and terrain tiles (`.tr4`), then renders the network
as an interactive Leaflet map you can pan, search, and align by hand.

- **`output_generator.py`** — reads a config INI and writes a self-contained
  `output/<name>/` (an `index.html` viewer + `manifest.json` + per-region JSON).
- **`serve.py`** — serves that output **and** lets you add/edit/move area labels
  live in the browser (see below).

---

## Requirements

- **Python 3** (the standalone viewers vendor Leaflet, so no extra Python packages
  are needed just to view/serve a generated map).
- A **Run8 Train Simulator V3** installation — the config points at its route
  directories (`.r8` track/signal files) and terrain tiles (`.tr4`). Edit the paths
  in a `config-*.ini` to match your install.

---

## 1. Generate a map

```bash
python output_generator.py config-socal-align.ini
```

This writes to the `[output] output_dir` named in the config (for
`config-socal-align.ini` that is `./output/socal_align/`). The no-flag default is
the **manual-alignment viewer**: the contiguous tile-based track drawn over a real
OSM / Satellite basemap that you slide into place by eye. (Other modes:
`--production`, `--minimal`, `--tile-based` — see the table below.)

---

## 2. Edit area labels live  ⭐

Area labels (yard names, control points, junctions, region names, notes) can be
added, edited, moved, and rotated **directly on the map** — no copy-paste and no
regenerate. Use the bundled **`serve.py`** instead of a plain static file server.

**One-time setup:** the config must name a writable areas file, e.g. in
`config-socal-align.ini`:

```ini
[visualization]
areas_file = areas_socal-pr.ini
```

(that file may be empty to start — or pass `--areas-file PATH` to `serve.py`).

**Run it** (from the project root — *not* from inside the output directory):

```bash
python output_generator.py config-socal-align.ini      # generate once (step 1)
python serve.py config-socal-align.ini --port 8000      # then serve + author
```

Open **http://127.0.0.1:8000/** and hard-reload (Ctrl+Shift+R). `serve.py` serves
the generated output and exposes a small REST API the viewer uses.

**In the viewer:**

- **Add:** toggle the **"Add Label"** button → click to place → click a second point
  along a track to set the angle (or **Esc** for horizontal) → fill in text, **Type**
  (Yard / CP / JCT / Region / Notes / Other — new labels default to **CP**), font, box,
  and **Color** → **Save**. The Color dropdown offers **Default** (the category's
  color), the named **presets** (BNSF, UP, …), or **Custom** (an RGB picker). Presets
  are stored by name (see below), so recoloring a preset updates every label using it.
- **Edit / Delete:** **click** an existing label → popup with fields + **Delete**.
- **Move:** **drag** a label.
- **Rotate:** **hold the mouse button on a label and scroll the wheel** (Shift = 1°
  fine steps).
- **Show/hide by category:** next to the **Area Labels** checkbox, the **Filter**
  button opens a popover of per-category checkboxes (with All / None), so you can show
  just control points, just yards, etc.

Every change is written straight into the writable areas file (atomic write, with a
`.bak` backup) and the served `manifest.json` is kept in sync, so a fresh reload
matches. The INI stays the source of truth — a later `output_generator.py` run
reproduces everything.

`serve.py` options:

```
python serve.py <config.ini> [--port 8000] [--host 127.0.0.1] [--areas-file FILE] [--no-authoring]
```

- `--areas-file FILE` — write to this areas file instead of the config's first
  `areas_file` entry.
- `--no-authoring` — serve read-only (hides the "Add Label" button; existing labels
  are non-interactive). Same as hosting the `--production` build.

> **Without `serve.py`** (e.g. `python -m http.server`, or opening the file directly),
> the viewer detects no backend and falls back to the legacy flow: the add-label
> popup gives you a paste-ready `[area.*]` INI block to drop into your areas file,
> after which you re-run `output_generator.py`. Existing labels are not editable in
> that mode.

### Area label categories

Each label has a `type` — one of the categories your config defines (see
`[label_types]` below). The category sets a default text color and drives the
per-category show/hide toggles. A label whose `type` isn't defined (or is missing)
renders **white** and groups under an auto **"Other"** toggle. Full label syntax:

```ini
[area.barstow_yard]
label = Barstow Yard
tile = 209,-10           ; tile_x,tile_z
local = 421.5,-500.2     ; Run8 local_x,local_z within that tile
type = yard              ; optional: a [label_types] id (undefined -> white)
color = bnsf             ; optional; a preset name or hex; defaults to the type's color
font_size = 16           ; optional
box = true               ; optional background box
rotation = -30           ; optional degrees clockwise
```

**Color palette (config-owned).** No categories or colors are baked into the code —
the config defines them. Two optional sections:

```ini
[label_types]            ; id = Display Name, #color  (order = filter order)
yard    = Yard, #ffd11a
cp      = CP, #ff6b35
region  = Region, #ffffff
station = Station, #0078b9

[color_presets]          ; named presets for the label color dropdown
bnsf = #f85d13
up   = #ffcc00
; add your own railroads here
```

The display name shows in the filter/editor; the color is the category default (a
label's own `color=` still wins). If a label uses `color = bnsf` but no
`[color_presets]` defines `bnsf`, it renders as that literal text — so define every
preset your labels reference.

---

## Output modes

Pass one flag to `output_generator.py <config.ini>`:

| Flag | Viewer |
|------|--------|
| *(none)* | **Manual-alignment viewer (default).** Tile-based track over a real OSM/Satellite basemap (+ OpenRailwayMap overlay), draggable to align by eye. Full overlays, search, area-label display **and authoring**. |
| `--production` | Same as default, but hides the "Add Label" button (for hosting to end users). |
| `--minimal` | Geographic viewer using the per-tile bilinear georeference with `tile_corrections.csv` applied. |
| `--tile-based` | Flat tile-based viewer (no real-world basemap). |

> Manual alignment is the default because the Run8 route is a *topological* model —
> section lengths are compressed/stretched and a few tiles carry route-designer
> defects — so no single automatic transform georeferences the whole network. See
> `openrailways_goals.txt`.

---

## Notes

- The `--minimal` (geographic) mode uses a `tile_corrections.csv` to nudge tiles
  toward real-world coordinates; it is approximate.
- Industry info comes from each region's `Config.ind`; AI locations come from the
  per-route `AiSpecialLocation` files.
- More detail on internals, config options, and the label pipeline is in
  [`CLAUDE.md`](CLAUDE.md).
