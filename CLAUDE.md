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

**Output modes** — the **manual-alignment viewer is the only viewer**. (The former
`--minimal` geographic and `--tile-based` flat viewers were removed 2026-08-30: both
were subsets of the align viewer, which reproduces the undistorted contiguous grid when
its Base Map is set to None. The align viewer's tile-coordinate *extraction* stays — it
is what the viewer is built on.)

| Flag | Viewer |
|------|--------|
| *(none)* | **Manual-alignment viewer (default).** The contiguous tile-based track drawn over a real OSM / Satellite map (with an OpenRailwayMap overlay), draggable by eye to align a chosen area. Includes all overlays, search, the local-symbol filter, Area Label display, and Area Label authoring. |
| `--production` | Same as the default, but hides the "Add Label" authoring button (for hosting the map to end users). |
| `--align` | Explicitly selects the default viewer; now a no-op kept for backward compatibility. |

> **Why manual alignment is the default:** the Run8 route is a *topological* model — section lengths are compressed/stretched and a few tiles carry genuine route-designer defects — so no automatic transform georeferences the whole network. The default viewer instead renders the internally-consistent (contiguous) tile grid on a real map and lets you slide it into place per area of interest. See `openrailways_goals.txt` for background.

#### World saves: plotting trains & rail vehicles
A Run8 world save (`.xml`, e.g. `small_world.xml`) can be plotted on the track:

```bash
python output_generator.py <config.ini> --world <world_save.xml>
```

`--world FILE` overrides an optional `[visualization] world_save = FILE` in the config
(resolved relative to the config dir). Each train lives under
`//trainList/TrainLoader/unitLoaderList/RailVehicleStateClass`; every vehicle reports two
*trucks* (paired child order = A, B) with `currentTrackSectionIndex`, `startNodeIndex`,
`distanceTravelledInMeters`, `currentRoutePrefix`, plus `unitNumber` / `destinationTag` /
`unitType`. Parsing is in **`world_parser.py`** (tolerant: missing values → 0/false/0.0).

**Placement** (`region_extractor.extract_trains` / `_SectionPlacer`): a truck is placed by
walking `distanceTravelledInMeters` **metres** along that section's already-extracted
`SectionData.paths` polyline, oriented by `startNodeIndex` (0 → from `polyline[0]`, else from
the far end) and clamped to the section length. Because both the Run8 distance and the
tile-world coords are in metres, no fraction/denominator is needed. Each **car is drawn as a
body polyline spanning its two trucks** (so it follows curves); a vehicle's ordering key is the
yard-direction-most (minimum) truck distance — geometry only (the YARDS logical-yard-track
`SEQ` layer in `world-import-rail-vehicle-ordering.md` is intentionally **not** reproduced, as
it needs the `.ind` survey this tool does not consume).

**Within-consist de-overlap** (`[trains] deoverlap`, default **on**; `extract_trains(...,
deoverlap=True)`). Raw per-truck placement draws cars that overlap or streak when a truck lands
on a broken section or the two trucks straddle a curve. Because a coupled consist is a *rigid*
string, `_layout_consist` instead lays each consist's cars **end to end along its section
chain**: cars in world-save (front→back) order — trusted as the physical coupling order — each
at its **full coupled footprint** (`RV_LENGTH`) so footprints abut like a real train and the laid
length matches the true track span (near-zero drift), with each body drawn **inset by one coupler
per end** so the couplers reappear as the visible inter-car gap. Each body is a **straight chord**
between its two end points on the chain (a rail car is rigid, so it must not bend), which keeps a
car spanning a switch/curve a straight rectangle instead of wrapping onto the diverging leg;
consecutive cars still abut because their chord endpoints share a chain point. The consist's
occupied sections are chained into one continuous polyline via endpoint adjacency
(`_build_consist_polyline`; for placement, `_concat_section_polyline` uses the straight chord
between a **switch** section's canonical endpoints — its detailed path bows ~1 m toward the
diverging lead, which would tilt a through-route car onto the diverging leg — and snaps a
non-switch detailed path's endpoints to its node positions so seams meet cleanly; the *map* still
draws every path), split into contiguous **runs** at any
degenerate/missing/non-adjacent joint (`_split_runs`) so a single bad section can't stretch the
whole train; each run is anchored at the **median** of its cars' true positions (midpoint anchor).
Cars on an unusable section fall back to per-truck placement
(`_fallback_body`). This still trusts XML order *within* one consist only — no cross-cut / logical
yard-track ordering (that needs the `.ind` survey; see `rv_cross_section_ordering_plan.md`).

> **Depends on an honest *placement* polyline.** A section's `paths` may hold the same span
> multiple times (coarse forward + coarse mirror + detailed) and/or distinct switch legs. The
> **map draws all of them** (so turnouts render every leg), but *placement* needs one
> non-repeating route. `_concat_section_polyline` (used only by `_SectionPlacer`, never the map)
> now takes the **most-detailed path as the base and stitches on only paths that *continue* it
> end-to-start**, skipping duplicates, reverses (same endpoints) and branches (a switch's other
> leg). Previously it concatenated *every* path into an out-and-back / zigzag polyline of up to
> ~5× the real length (a *degenerate* start==end polyline when the mirror halves cancel — section
> 2283), which mis-placed trucks and injected a spurious ~1.3–1.5× "stretch" that made the rigid
> layout drift. Fixing this at the placement layer (not by dropping paths, which would erase
> turnout legs from the map) is what lets the footprint-abutting layout stay on the true track.

**Cross-region cars.** Run8 section indices are **per-region and collide** (Barstow's 446 ≠
Needles' 446), and `currentTrackSectionIndex` is paired with `currentRoutePrefix`. So the placer
is keyed by **`(route_prefix, section_index)`** and each truck is resolved by its *own*
`truck.route_prefix` — a car whose two trucks are in different regions (e.g. straddling the
Barstow/Needles seam) draws correctly across the boundary (all regions share one tile-world
coordinate space). Placement therefore runs **once over all regions**: `build_section_placer`
builds the combined placer from every region's `(prefix, sections)`, and `extract_trains(trains,
placer, rv_lengths)` returns `{route_prefix: [TrainData]}` — each car assigned to the region of
its **truck-A** prefix (so a train spanning regions is split into per-region `TrainData`; the
car bodies span the seam, but a train's spine is still drawn per region). `output_generator`
extracts all regions first, then places trains and attaches `RegionData.trains`; `extract_region`
no longer places trains. Emitted to the region JSON as `trains` (`train_to_dict` /
`rail_vehicle_to_dict`).

**True vehicle length** (optional): with `[visualization] railvehicle_db = db_railvehicles.db`
(a SQLite DB — `loco_data` / `car_data` tables keyed by `R8_FILENAME`, columns `RV_LENGTH` and
`COUPLER_OFFSET`, both feet), the body is grown from the truck-to-truck span toward the real
vehicle length. `RV_LENGTH` is the *coupled footprint* (over pulling faces) — drawing it whole
makes coupled cars **abut with no visible gap**, so we draw the car **body between the couplers**:
`body = RV_LENGTH - 2 * COUPLER_OFFSET`, extended past each truck by
`overhang = (body_m - truck_span) / 2` (kept centred on the trucks). The dropped couplers become
the gap between adjacent cars. Loaded by **`rv_length_db.load_rv_lengths()`** (→
`{rvXMLfilename.lower(): (length_m, coupler_offset_m)}`) and passed to
`extract_trains(..., rv_lengths=...)`. Only meaningful in the metre-based (tile/align) coord mode;
without the DB the body stays the truck-to-truck span.

**Viewer:** a **Trains** overlay (each car a body polyline; length is the true car length when
`railvehicle_db` is set, otherwise the truck-to-truck span) with per-vehicle popups (train id / unit / type / destination) and
a monospace tooltip (`Train : <id>` / `RV num : <unit>` / `RV tag : <destination>` /
`RV typ : <car_type>`, then a divider and whole-consist totals: `Cars` / `Length` (ft) /
`Trail` = trailing tons (cars only) / `Total` = total consist weight incl. locomotives),
and the popup shows the DB car type plus the same consist totals. The totals describe the
**entire** world-save train, so hovering any car (or a region-straddling train's far half)
reports the full length/tonnage; they need `railvehicle_db` for length + tare weight and the
save's per-vehicle `loadWeightUSTons` for lading (gross = tare + load). Body colours are
config-driven. A
**locomotive** is coloured by its **owning railroad** from `[loco_company_colors]` (keyed by the loco
DB's `INITIAL` reporting mark, e.g. `BNSF`, `ATSF`, `SP`, `UP`, `CSXT`, `R8W`; keys case-insensitive),
falling back to `[colors] train_loco` when a mark has no entry (or the loco isn't in the DB). Every
other car is coloured by **car type** from `[car_type_colors]` (keyed by the DB's
`INDUSTRY_CONFIG_CAR_TYPE`, e.g. `Tank_Car`, `Covered_Hopper`, `Box_Car`; keys case-insensitive), falling
back to `[colors] train` when a type has no entry. Both maps are parsed to
`VisualizationConfig.car_type_colors` / `loco_company_colors`, emitted as `manifest.car_type_colors` /
`manifest.loco_company_colors`, and applied by `rvBodyColor` in `renderTrains`
(`isLoco ? locoCompanyColor(v.company) || trainLoco : carTypeColor(v.car_type) || train`). `car_type`
and `company` per vehicle come from `rv_length_db` (`RvInfo.car_type` — locos are `Locomotive`;
`RvInfo.company` — loco `INITIAL`, cars `""`) via `extract_trains`, emitted on each RV in the region JSON.
A **locomotive is drawn as one arrow polygon** (a body rectangle with a pointed nose at its
front end; `locoArrowLatLngs` / `_locoArrow` in `generate_javascript`) instead of a car's plain
body line, so the powered unit and its facing read at a glance. The nose points at the loco's
front (`v.front0`, viewer-flippable via `LOCO_FACING_FLIP`). The polygon is built in pixel space
so its width matches the car bodies (`rvBodyWeightPx`) and is rebuilt on zoom by
`updateTrainWidths`, while its length stays geographic. This replaced the earlier pill body +
white facing triangle. (The zoomed-out collapsed-train view keeps its own lead-loco arrow.)
Cars stay blunt body lines; all vehicles share one line weight.
Each **train** (cars sharing a `train_id`) also gets a **thin connecting spine** through its cars, so a
consist reads as one unit (`drawTrainOutline` in `generate_javascript`: cars are chained by
nearest-neighbour so the spine follows the train even across sections / mis-ordered XML; the spine runs
end tip -> each car centre -> other end tip; colour `[colors] train_outline`, default black).
Line widths come from an optional `[trains]` section, injected as `window.TRAIN_STYLE`
(`{{car, spine, carM}}`) and read by `renderTrains` / `drawTrainOutline`:
- `spine_width` (default 1.5) — connecting-line width in **px** (zoom-invariant).
- `car_width` (default 7) — RV body **min** width in px (the floor at low zoom).
- `car_width_m` (default 3.5) — real RV width in **metres**; the body widens with zoom to this
  (`rvBodyWeightPx()` = `car_width_m / metres-per-pixel`, floored at `car_width`, capped 64 px,
  re-applied on `zoomend` via `updateTrainWidths`). Because it scales with zoom like the map and
  the raster rail do, the RV stays wider than the (fixed-5px vector, zoom-scaling raster) track at
  every zoom. `car_width_m = 0` reverts to a plain fixed `car_width` px. Config-side floats strip
  inline `;`/`#` comments (default ConfigParser keeps them, which would break `float()`).
At close zoom each RV also shows its **destination tag centered on the car** (`trainDestLabelIcon`
divIcons in a per-region `layers.trainLabels` group; `updateTrainLabelVisibility()` adds/removes the
group on `zoomend` and when the Trains overlay toggles — labels show only when Trains is on and the
scale bar reads `[trains] label_scale_m` metres or tighter, default 30). The threshold is compared
against `_scaleBarMeters()`, which mirrors Leaflet `L.control.scale`'s 1/2/3/5x10^n rounding (so it
matches the on-screen bar exactly — note the **3** step: `< 0.5` m/px lands on the 30 m bar, not 20).
A **Train / Rail
Vehicle** search type matches **trainID**, **destinationTag**, or **unitNumber** (a hit
enables the overlay and pans to the vehicle). Wired in the shared base
(`generate_javascript`: `renderTrains`, overlay entry, search) which the align viewer reuses,
plus `transformData` in `ALIGN_JS`.

An **Area Label** search type (`performSearch`/`goToResult` in the shared base) matches an
area label's **text** or **id** against `MapApp.areaMarkers` (align-viewer only). A hit makes
the Area Labels overlay + the label's category visible, pans to the marker, and briefly
flashes it (`flashAreaMarker`, a CSS `filter` glow - never `transform`, which Leaflet owns for
positioning/rotation). Guarded with `typeof` checks so the base build degrades gracefully.

##### Live world-save watching (`serve.py --world`)
```bash
python serve.py <config.ini> --world <world_save.xml> [--no-authoring]
```
`serve.py` watches the world save's mtime and re-places vehicles when it changes,
reconstructing section polylines from the already-generated region JSON (no track-DB
reload). New endpoint `GET /api/trains` → `{version, trains:{regionId:[...]}}` (`version`
is the file mtime; results are cached until it changes). The align viewer probes
`/api/ping` (now also reports `world`) and, when a watched save is present, **polls
`/api/trains` every 3 s** and re-renders the Trains layer, so editing/re-saving the world
in Run8 moves the vehicles on the map with no reload. Static output (served without
`serve.py`) still shows the trains baked into the region JSON, just without live refresh.
Committing a manual alignment (`rerenderAlign`) rebuilds every region from the raw cache, which only
has the *baked* trains, so after the reload the poll is re-run (`pollTrainsOnce`, with the version guard
reset) to restore the live positions - otherwise live-only trains would vanish on align.

**Output Structure:**
```
output/<name>/
├── index.html          # Interactive map viewer (see Output modes above)
├── manifest.json       # Region metadata, area labels, alignment seed + tile params
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
# Optional: initial opacity slider values (percent 0-100 or 0-1 fraction; defaults map 20, track 80)
initial_map_opacity = 20
initial_track_opacity = 80

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
train = #8B0000            # rail-vehicle (car) body from a world save
train_loco = #B22222      # locomotive body
train_outline = #000000   # thin spine joining a train's cars

[output]
output_dir = ./output/socal/
```

#### Area/Place Labels
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
type = yard              ; optional category: a [label_types] id (undefined -> white)
```
Labels scale with the map: full `font_size` at the 50 m scale-bar level, shrinking to
a small floor by ~15 km (tunable in `updateAreaLabelSizes` in html_generator.py).
Labels are emitted into `manifest.json` (`areas`) and rendered as a toggleable
"Area Labels" overlay in the manual-alignment viewer.

**Categories (`type`) — config-defined.** Categories come from the config's
`[label_types]` section (`id = Display Name, #color`, ordered), parsed into
`VisualizationConfig.label_types` (a list of `LabelType`) and emitted to
`manifest.label_types` (`[{id,name,color}]`). A label's `type` is a category id; a type
**not** present in `[label_types]` (or a missing type) renders in `UNDEFINED_TYPE_COLOR`
(white) and groups under the viewer's `AREA_UNDEFINED` (`__other__`) bucket. There is no
hardcoded category list — `AREA_TYPES`/`AREA_TYPE_LABELS` were removed; the viewer builds
everything from `manifest.label_types` (`labelTypes`/`labelTypeColor`/`areaTypeOf` in
`ALIGN_JS`). In the **align viewer** the "Area Labels" overlay is a master checkbox with a
**Filter** button beside it; the button opens a popover (`toggleAreaFilterPopover`) of
per-category checkboxes (colour-swatched, with All / None), **plus an auto "Other" row**
shown only when some loaded label is undefined (`hasUndefinedLabels`). The add/edit popup (and the
no-backend INI popup) `Type` selector lists the defined categories + an "Other" (undefined)
option. New labels default to **`cp`** if defined, else the first category (`newLabelType`).
Type validation was dropped everywhere (any string is accepted).

**Config-owned color palette (no colors baked into code).** Both palettes come from the
config, not from hardcoded defaults:
- *Per-category colors* — defined inline in `[label_types]` (the `#color` after each
  display name); emitted via `manifest.label_types`. An undefined category → white.
- *Named presets* — the `[color_presets]` section (`name = hex`). `DEFAULT_COLOR_PRESETS`
  is now **empty** (no built-in `bnsf`/`up`; the shipped configs define them), exposed as
  `VisualizationConfig.color_presets` and emitted to `manifest.color_presets`. A label
  `color` may be a preset **name** or raw hex. **The name is stored, not the hex** — kept raw in
the INI / `AreaLabel.color` / REST payload and **resolved to hex at render time** by the
viewer (`resolveColor` in `ALIGN_JS`), so recoloring a preset updates every label that uses it. In the
align editor the Color control is a dropdown — **Default** (the category color), the
named presets, or **Custom** (a native RGB `<input type="color">`) — with a live swatch;
Default stores no color, a preset stores its name, Custom stores hex. `config_parser.resolve_color()`
is the Python-side resolver (used for tests / any server-side rendering).

Authoring (capturing new labels), in the manual-alignment viewer:
- Toggle the **"Add Label"** button (mutually exclusive with "Align mode"; hidden
  entirely under `--production`), then **click** to place.

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

#### Signals (dispatcher-style glyphs)
Signals render as a **circle + T** glyph (a head with a mast and one crossbar per head,
the crossbar pointing the way the signal faces — same direction the old triangle tip
pointed, `rotation + 180°`). Fill colour = type (`absolute`/`intermediate`); **stacked
heads add extra, shorter crossbars** (no ring-colour change). Everything is a canvas
vector (`preferCanvas`) built in metres, so there is no DOM per signal and no perf hit at
scale. Code: `renderSignals` / `signalGlyphGeom` in `ALIGN_JS`.

- **Drawn at the true position (Run8 is ground truth).** The `.r8` `SignalHead.position` is
  the real mast location, already offset ~3.5 m to the **correct side** of the governed
  track (97% of signals sit >2 m off centreline; the side — including same-facing pairs on
  *opposite* sides — is in the data). The glyph is drawn at that true point, so there is no
  artificial offset, side-guessing, overlap-flip, or manual placement. Zoomed out, signals
  merge onto the line, which is expected. Config: `[signals] size_m` (glyph length, 7.5 m).
- **Outline colour is theme-aware.** The mast/head outline is `[colors] signal_border_single`
  (black) on the light map but a light grey (`#e8e8e8`) in night mode, since black
  disappears there (`signalOutlineColor`). Canvas colours are baked at draw time, so
  `applyTheme` calls `rerenderSignals` when the theme flips.
- **Stacked dedupe.** `extract_signals` emits a stacked signal as ONE record listing every
  member id in `stacked_ids` PLUS a solo record per member; the viewer draws **one
  representative per stack** and drops the members' solo records (each member id still maps
  to the rep's marker so search by any member id works). Without this a stacked signal is
  also drawn as a phantom lone signal.
- **Type filter.** A **Filter** button beside the Signals overlay row opens an
  **Absolute / Intermediate** popover (`addSignalFilterButton` / `toggleSignalFilterPopover`
  / `setSignalTypeVisible`, re-renders via `rerenderSignals`), mirroring the Area Labels
  filter (and the Trains "Filter" button). Intermediates are the usual clutter; hiding them
  gives a dispatcher-style view. Initial state: `[signals] show_intermediate` (default true),
  emitted as `manifest.signal_show_intermediate`.

#### Interactive Map Features
- **Base Map Selection**: OpenStreetMap, Satellite (Esri), or None, plus a toggleable **OpenRailwayMap** overlay
- **Region Toggle**: Enable/disable regions dynamically (data loaded on demand); in the align viewer, enabling a region fits the map to it
- **Overlay Controls**: Toggle Signals, Industries, AI Locations, Tile Boundaries, and Area Labels
- **Search Function**: Search by track section, signal, industry tag, AI location, area label, or train/rail vehicle
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
