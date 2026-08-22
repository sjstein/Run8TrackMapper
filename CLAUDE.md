# Run8 Track Mapper Project

## Project Summary
Tools for visualizing Run8 Train Simulator track networks on geographical maps. Parses binary track database (.r8) and terrain tile (.tr4) files to extract track sections with coordinates, then overlays them on interactive HTML maps using real-world lat/lon coordinates.

## Core Components

### Multi-Region Web Application (Primary)
**Status:** Working well - recommended for new visualizations

A web-based visualization system that supports multiple regions with dynamic loading.

**Key Files:**
- `output_generator.py` - Main entry point, generates manifest.json and per-region JSON files
- `html_generator.py` - Generates index.html with Leaflet.js map
- `region_extractor.py` - Extracts track, signal, industry, AI location, and tile data
- `config_parser.py` - Parses multi-region INI configuration files

**Usage:**
```bash
python output_generator.py <config.ini>
```

**Output modes** (select with a flag; the default is the manual-alignment viewer):

| Flag | Viewer |
|------|--------|
| *(none)* | **Manual-alignment viewer (default).** The contiguous tile-based track drawn over a real OSM / Satellite map (with an OpenRailwayMap overlay), draggable by eye to align a chosen area. Includes all overlays, search, the local-symbol filter, Area Label display, and Area Label authoring. |
| `--production` | Same as the default, but hides the "Add Label" authoring button (for hosting the map to end users). |
| `--minimal` | Geographic viewer using the per-tile bilinear georeference with `tile_corrections.csv` applied. This was the previous default (no-flag) behavior. |
| `--tile-based` | Flat tile-based viewer (`L.CRS.Simple`) with no real-world basemap. |

> **Why manual alignment is the default:** the Run8 route is a *topological* model — section lengths are compressed/stretched and a few tiles carry genuine route-designer defects — so no automatic transform georeferences the whole network. The default viewer instead renders the internally-consistent (contiguous) tile grid on a real map and lets you slide it into place per area of interest. See `openrailways_goals.txt` for background.

**Output Structure:**
```
output/<name>/
├── index.html          # Interactive map viewer (see Output modes above)
├── manifest.json       # Region metadata, area labels, and mode-specific params
│                       #   (align: alignment seed + tile params; minimal: tile corrections)
└── data/
    └── <region_id>.json  # Per-region track, signal, industry, AI, and tile data
```

#### Multi-Region Configuration File
```ini
[visualization]
name = Southern California
tile_corrections = tile_corrections.csv
industry_db = C:\Run8Studios\...\Regions\SouthernCA\Config.ind
# Optional: initial map center as lat,lon
initial_center = 34.9,-118.0

[region.mojave]
display_name = Mojave Subdivision
route_prefix = 100
directory = C:\Run8Studios\...\BNSF_MojaveSub
enabled_by_default = true
# Optional: per-region track color (overrides global default)
track_color = #0066cc

[region.barstow]
display_name = Barstow Subdivision
route_prefix = 200
directory = C:\Run8Studios\...\BNSF_BarstowSub
enabled_by_default = false
track_color = #2266ff

[colors]
# All colors are optional - defaults shown
track = #0066cc
track_selected = #ff0000
switch = #800080
industry_track = #00aa00
signal_absolute = #FF6B35
signal_intermediate = #FFD700
signal_border_single = #000000
signal_border_stacked = #87CEEB

[output]
output_dir = ./output/socal/
```

#### Area/Place Labels (tile-based mode)
Optional `[area.*]` sections add always-on text labels (yards, towns, junctions,
control points) at a tile + Run8 local coordinate. They may live inline in the config
or in external file(s) referenced by `[visualization] areas_file = a.ini, b.ini`
(comma-separated, resolved relative to the config dir); inline and external labels are
merged and duplicate ids are rejected. Same syntax either way:
```ini
[area.barstow_yard]
label = Barstow Yard
tile = 209,-10           ; tile_x,tile_z
local = 421.5,-500.2     ; Run8 local_x,local_z within that tile
color = bnsf             ; optional; a [color_presets] name or hex; defaults to the type's color, then [colors] area_label
font_size = 16           ; optional (size at <=50 m scale; default 22)
box = true               ; optional, background box behind the text (default: no box)
rotation = -30           ; optional, rotate text in degrees clockwise (align to track/yard)
type = yard              ; optional category: yard|cp|jct|region|notes|other (default other)
```
Labels scale with the map: full `font_size` at the 50 m scale-bar level, shrinking to
a small floor by ~15 km (tunable in `updateAreaLabelSizes` in html_generator.py).
Labels are emitted into `manifest.json` (`areas`) and rendered as a toggleable
"Area Labels" overlay in **both** the tile-based viewer and the manual-alignment
(default) viewer.

**Categories (`type`).** Each label has a category — `yard`, `cp` (control point),
`jct` (junction), `region`, `notes`, or `other` (the default for untyped/legacy
labels). A category implies a default text color (`[colors] area_yard`, `area_cp`,
`area_jct`, `area_region`, `area_notes`, `area_other`; a label's own `color=` still
wins) and drives per-category show/hide. In the **align viewer** the single "Area
Labels" overlay becomes a master checkbox with one indented child (colour-swatched)
per category, so you can show just control points, just yards, etc. The authoring
add/edit popup (and the no-backend INI-generator popup) include a **Type** selector.
The canonical category list + labels live in `AREA_TYPES`/`AREA_TYPE_LABELS`
(config_parser.py) and the `AREA_TYPES` JS constant in `ALIGN_JS` (html_generator.py);
default colors live in `ColorConfig` and are emitted to `window.COLORS.areaTypes`. The
tile-based viewer color-codes by category but keeps a single "Area Labels" toggle.
New labels placed in the align editor default to category **`cp`** (`AREA_TYPE_DEFAULT_NEW`
in `ALIGN_JS`); the *data* default for untyped/legacy labels stays `other`.

**Color presets (a palette).** A label `color` may be a **preset name** (e.g. `bnsf`)
or a raw hex. Presets are a `name -> hex` map defined in an optional `[color_presets]`
config section, merged over the built-ins `DEFAULT_COLOR_PRESETS` (`bnsf` `#f85d13`,
`up` `#ffcc00`) in config_parser.py, exposed as `VisualizationConfig.color_presets` and
emitted to `manifest.color_presets`. **The name is stored, not the hex** — kept raw in
the INI / `AreaLabel.color` / REST payload and **resolved to hex at render time** by the
viewer (`resolveColor` in `ALIGN_JS`; an inline lookup in the tile-based
`createAreaLabelIcon`), so recoloring a preset updates every label that uses it. In the
align editor the Color control is a dropdown — **Default** (the category color), the
named presets, or **Custom** (a native RGB `<input type="color">`) — with a live swatch;
Default stores no color, a preset stores its name, Custom stores hex. `config_parser.resolve_color()`
is the Python-side resolver (used for tests / any server-side rendering).

Authoring (capturing new labels):
- **Tile-based viewer:** **Shift+Click** the map to place a label.
- **Manual-alignment viewer:** toggle the **"Add Label"** button (mutually exclusive
  with "Align mode"; hidden entirely under `--production`), then **click** to place.

Then **click a second point along a track** to set the text angle (or press **Esc**
to leave it horizontal). Positions are converted to world meters via
`convert_run8_to_tile_coords()` (region_extractor.py); the capture tool inverts that
transform (the align viewer additionally inverts its manual-alignment transform to
recover world coords), and the angle is the screen bearing of the two clicks folded
to [-90, 90] so text stays upright.

##### Live authoring server (`serve.py`) — recommended for the align viewer
Instead of hosting the output with `python -m http.server` (a static file server
that can only hand out files), run the bundled **`serve.py`**, a stdlib-only server
(no Flask/extra dependency) that serves the same output **and** accepts label edits:

```bash
python serve.py <config.ini> [--port 8000] [--host 127.0.0.1] [--areas-file FILE] [--no-authoring]
```

With it running, the manual-alignment viewer's authoring popups gain a **Save**
button (new labels) and, on **click of any existing label**, an **edit popup**
(text / color / font / rotation / box) with a **Delete** button — full add/edit/delete
with no copy-paste and no regenerate. Existing labels can also be repositioned/rotated
directly on the map: **drag** a label to move it, and **hold the mouse button on** a
label while **scrolling the wheel** to rotate it (hold **Shift** for 1&deg; fine steps).
Rotation only occurs while the button is held (the wheel is captured so the map does not
zoom), and both gestures persist their change. Each change is written straight into the
**writable areas file** (the first entry of `[visualization] areas_file`, or
`--areas-file`), and the served `manifest.json` is kept in sync so a fresh reload
matches. The INI file stays the source of truth, so a later `output_generator.py`
run reproduces everything.

- **Detection is automatic:** the viewer probes `GET /api/ping`; if there's no
  backend (opened as a static file, or served by plain `http.server`) it silently
  falls back to the old **Generate INI → copy/paste** popup for new labels, and
  existing labels are non-interactive. `--production` (no "Add Label" button) is
  unaffected either way.
- **File safety:** writes are atomic (temp + replace) and back up the previous
  contents to `<areas_file>.bak` before each change; only the exact `[area.<id>]`
  block a mutation targets is rewritten, so comments/order elsewhere are preserved.
  Editing/deleting is only allowed for labels that live in the writable areas file
  (labels defined inline in the config or in another areas file return a clear error).
- **Code:** `serve.py` (HTTP + REST API: `GET/POST /api/areas`, `PUT/DELETE
  /api/areas/<id>`, `GET /api/ping`), `area_store.py` (formats/splices `[area.*]`
  blocks with atomic write + backup), and `config_parser.collect_areas()` /
  `parse_areas_file()` / `resolve_areas_files()` (merge/read the label set). Viewer
  wiring lives in `ALIGN_JS` in `html_generator.py` (`detectBackend`, `apiArea`,
  `openAreaEditor`, `addAreaMarker`/`replaceAreaMarker`/`removeAreaMarker`).

Without the server, the flow is still: popup **Generate INI** → paste the `[area.*]`
block into the areas file → re-run `output_generator.py`.

#### Interactive Map Features
- **Base Map Selection**: OpenStreetMap, Satellite (Esri), or None, plus a toggleable **OpenRailwayMap** overlay
- **Region Toggle**: Enable/disable regions dynamically (data loaded on demand); in the align viewer, enabling a region fits the map to it
- **Overlay Controls**: Toggle Signals, Industries, AI Locations, Tile Boundaries, and Area Labels
- **Search Function**: Search by track section, signal, industry tag, or AI location
- **Ctrl+Click Selection**: Select multiple track sections to calculate total length
- **Mouse Position**: Lat/lon display in lower right corner
- **Right-click → Google Maps** *(align viewer)*: right-click any point to open Google Maps at that lat/lon (with the current zoom) in a new tab, for cross-checking against real-world imagery/streetview. The coordinate is the map position under the cursor, so in the align viewer it is only as accurate as the current manual alignment.
- **Opacity Sliders**: Independent **Map Opacity** (base map + ORM) and **Track Opacity**
- **Align mode** *(align viewer only)*: drag the track to slide it onto the real map; releasing commits the new alignment
- **Add Label** *(align viewer only, hidden under `--production`)*: click to author a new area label and generate its `[area.*]` INI block

#### Visual Elements
- **Track Sections**: Configurable color, switches shown in different color
- **Industry Tracks**: Change color when industry overlay is enabled
- **Signals**: Directional triangles showing facing direction
  - Fill color: Orange (absolute) or Gold (intermediate)
  - Border color: Black (single head) or Light blue (stacked/multiple heads)
- **Tile Boundaries**: Black (normal) or Red (corrected) rectangles


| Section | Key | Required | Description |
|---------|-----|----------|-------------|
| `[database]` | `track_database` | Yes | Track database file path |
| `[database]` | `start_section` | Yes | Starting track section (must be a turnout) |
| `[database]` | `depth` | Yes | Number of switch levels to traverse (≥1) |
| `[optional_layers]` | `signal_db` | No | Signal database file (.r8) |
| `[optional_layers]` | `industry_db` | No | Industry database file (.ind) |
| `[optional_layers]` | `ai_locations` | No | AI spawn locations file (.r8) |
| `[filtering]` | `route_prefix` | Conditional | Required if `industry_db` or `ai_locations` used |
| `[output]` | `output_pattern` | No | Output filename pattern with placeholders |

#### Interactive Map Features
- **Layer Controls**: Toggle visibility of Tile Boundaries, Industries, AI Locations, Signals
- **Search Function**: Click magnifying glass (upper-left) to search by:
  - Track section number
  - Signal head number
  - Industry tag (case-insensitive)
- **Ctrl+Click Selection**: Select multiple track sections to calculate total length
- **Background Opacity**: Slider to adjust base map transparency
- **Legends**: Context-sensitive legends appear when relevant layers are enabled
  - Signal legend: Shows Absolute vs Intermediate signal types
  - Tile legend: Shows Normal vs Re-aligned tile boundaries

### util/explore_trackdb.py
**Status:** Working - core library
- Parses Run8 .r8 track database files
- Defines data structures: TrackNode, TrackSection, TrackDatabase
- Interactive shell for exploring track data
- Used as import by other scripts

### tile_corrections.csv
CSV database of tile coordinate corrections with columns: tile_x, tile_z, lat_offset, lon_offset

## Tech Stack
- **Python 3** (executable scripts use env python/python3)
- **folium** - Interactive map generation (Leaflet.js wrapper)
- **Standard library:**
  - `struct` - Binary data parsing
  - `zlib` - TR4 tile decompression (raw DEFLATE)
  - `csv` - Correction database I/O
  - `configparser` - INI configuration file parsing (visualize_switch_network.py)
  - `argparse` - CLI parsing (manage_tile_corrections.py, explore_trackdb.py)
  - `cmd` - Interactive shell (explore_trackdb)
  - `typing` - Type hints
  - `collections` - deque, Counter
  - `pathlib` - Cross-platform path handling

## File Formats

### .r8 Track Database Files
Binary format containing:
- Track sections with nodes
- Node data: tile indices (X, Z), positions, tangents, curve data
- Section connectivity (next_section indices)
- Switch positions and types

Located in various run8 region specific directories

### .tr4 Terrain Tile Files
Compressed binary (DEFLATE) containing:
- Tile textures and graphical assets
- Geographic boundaries (lat/lon) - extracted via "sentinel" pattern search
- Located at: `C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\TerrainTiles`

## Coordinate Systems

### Tile Coordinate System
- Integer grid: X, Z (e.g., -5, 44)
- Each tile has a .tr4 file named by coordinates

### Track Coordinates
- Positions relative to tile origin
- Must be transformed using tile boundaries to get lat/lon (if desired)
- 0,0 (X, Z) is defined as the lower left (SW) corner of a tile
- X values count up when moving East in units of meters
- Z values count down when moving North in units of meters
- Best guess tile dimensions are:
-- tile_width = 842.3
-- tile_height = 1023.2

## Code Style & Conventions

### Naming
- Snake_case for functions, variables
- PascalCase for classes (TrackNode, TrackSection, TrackDatabase)
- SCREAMING_SNAKE_CASE for constants
- Private module functions may use leading underscore

### Binary Parsing
- Use `struct.unpack()` with format strings
- Define sizes with `struct.calcsize()`
- Standard pattern:
  ```python
  fmt = '<i ii fff ...'  # little-endian format
  size = struct.calcsize(fmt)
  unpacked = struct.unpack(fmt, data[offset:offset+size])
  ```

### File Paths
- Use raw strings for Windows paths: `r'C:\...'`
- TILE_DIR constant points to terrain tiles

### Sentinel Pattern Searching
- TR4 files use "sentinels" (magic numbers) to mark data structures
- Sentinel-3 precedes tile boundary data at offset +40
- Search pattern: iterate through bytes, check for sentinel value

## Known Issues & TODOs

### Future Enhancements
- Allow plotting based only on tiles - no geographic constraints
- Decode more TR4 format details (textures, assets)

## Typical Workflows

TBD

## External Dependencies
- Run8 Train Simulator V3 installation required for tile files
- Default tile path: `C:\Run8Studios\Run8 Train Simulator V3\Content\V3Routes\Regions\SouthernCA\TerrainTiles`

## Data Enumerations

### AI Spawn Point Types
Used in AI special locations files (.r8):
| Value | Type Name |
|-------|-----------|
| 0 | Spawn Point |
| 1 | Crew Change |
| 2 | Crew Change & Hold |
| 3 | Passenger |
| 4 | Passenger Crew Change |
| 5 | Passenger Crew Change & Hold |
| 6 | Relinquish |
| 7 | Passenger Relinquish |
