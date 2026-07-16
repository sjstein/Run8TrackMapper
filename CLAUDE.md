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
python output_generator.py config_multiregion_sample.ini
```

**Output Structure:**
```
output/<name>/
├── index.html          # Interactive map viewer
├── manifest.json       # Region metadata and tile corrections
└── data/
    └── <region_id>.json  # Per-region track/signal/industry data
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
color = #ffd11a          ; optional (defaults to [colors] area_label)
font_size = 16           ; optional (size at <=50 m scale; default 22)
box = true               ; optional, background box behind the text (default: no box)
rotation = -30           ; optional, rotate text in degrees clockwise (align to track/yard)
```
Labels scale with the map: full `font_size` at the 50 m scale-bar level, shrinking to
a small floor by ~15 km (tunable in `updateAreaLabelSizes` in html_generator.py).
Labels are emitted into `manifest.json` (`areas`) and rendered as a toggleable
"Area Labels" overlay in the tile-based viewer. To capture coordinates, **Shift+Click**
the map to place a label, then **click a second point along a track** to set the text
angle (or press **Esc** to leave it horizontal). A popup then shows the tile/local/rotation
and generates a ready-to-paste `[area.*]` block. Positions are converted to world meters
via `convert_run8_to_tile_coords()` (region_extractor.py); the capture tool inverts that
transform, and the angle is the screen bearing of the two clicks folded to [-90, 90] so
text stays upright. Currently wired for tile-based output only (geographic mode not yet supported).

#### Interactive Map Features
- **Base Map Selection**: OpenStreetMap, Satellite (Esri), or None
- **Region Toggle**: Enable/disable regions dynamically (data loaded on demand)
- **Overlay Controls**: Toggle Signals, Industries, AI Locations, Tile Boundaries
- **Search Function**: Search by track section, signal, industry tag, or AI location
- **Ctrl+Click Selection**: Select multiple track sections to calculate total length
- **Mouse Position**: Lat/lon display in lower right corner
- **Background Opacity**: Slider to adjust base map transparency

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
