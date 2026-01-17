# Run8 Track Mapper Project

## Project Summary
Tools for visualizing Run8 Train Simulator track networks on geographical maps. Parses binary track database (.r8) and terrain tile (.tr4) files to extract track sections with coordinates, then overlays them on interactive HTML maps using real-world lat/lon coordinates.

## Core Components

### visualize_switch_network.py
**Status:** Working well
- Generates interactive HTML maps using Folium
- Traces track networks from a starting section to specified depth
- Applies tile corrections to align misaligned tiles
- Outputs: HTML files with track segments overlaid on maps
- **Configuration via INI file** (no command-line arguments for visualization settings)

Usage:
```bash
python visualize_switch_network.py                    # Use config.ini in current directory
python visualize_switch_network.py --config my.ini   # Use specified config file
python visualize_switch_network.py --generate-config # Print sample config template
```

#### Configuration File (config.ini)
```ini
[database]
track_database = track_databases/Mojave.r8
start_section = 5306
depth = 10

[optional_layers]
signal_db = track_databases/SignalHeads_Mojave.r8
industry_db = track_databases/Industries_Mojave.ind
ai_locations = track_databases/AISpecialLocations.r8

[filtering]
route_prefix = 1

[output]
output_pattern = {track_db}_{section}_depth{depth}.html
```

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

### manage_tile_corrections.py
**Status:** Working well
- Identifies misaligned terrain tiles by comparing edges with neighbors
- Generates/maintains tile_corrections.csv with lat/lon offsets
- Commands: `inspect`, `add`, `scan`, `scan-range`
- Each scan starts fresh - does not load existing CSV, calculates all corrections from trusted home tile
- Multi-pass algorithm propagates corrections through chains of misaligned tiles

Usage examples:
- `python manage_tile_corrections.py inspect <x> <z>` - View tile and neighbors
- `python manage_tile_corrections.py scan --track-db <file> --start-section <num>` - Scan from track database
- `python manage_tile_corrections.py scan-range <x_min> <x_max> <z_min> <z_max> --home-tile <x>,<z>` - Scan specific tile range

### explore_trackdb.py
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

Located in: `track_databases/` directory

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
- Must be transformed using tile boundaries to get lat/lon

### Geographic Coordinates
- Latitude/Longitude (WGS84)
- Bounds validation: CONUS coverage
  - LON: -130.0 to -65.0
  - LAT: 23.0 to 50.0

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

## Tile Correction Algorithm

### How It Works (scan-range)
1. **Pass 1 - Categorization:**
   - Loads all tiles in the specified rectangular range
   - Iteratively categorizes tiles as: trusted, needs correction, or uncategorized
   - Trusted tiles: aligned with home tile or other trusted neighbors (gap < threshold)
   - Needs correction: has trusted neighbors but not aligned (gap >= threshold)
   - Uncategorized: no trusted neighbors yet

2. **Pass 2+ - Correction Calculation:**
   - For each tile needing correction, calculate offset from all trusted neighbors
   - Averages suggestions from multiple neighbors for accuracy
   - Applies corrections virtually and adds to trusted set
   - Re-evaluates uncategorized tiles after each pass
   - Continues until no more corrections or max-passes reached

3. **Output:**
   - Saves all corrections to CSV with lat/lon offsets
   - Reports trusted tiles, corrections added, and remaining uncategorized tiles

### Key Design Decisions
- **Fresh start:** Each scan recalculates from scratch, ensuring consistency with chosen home tile
- **Multi-pass:** Allows corrections to propagate through chains of misaligned tiles
- **Neighbor averaging:** Multiple neighbors provide more accurate corrections
- **Full range scan:** scan-range loads all tiles upfront vs scan's BFS expansion

## Known Issues & TODOs

### Future Enhancements
- Decode more TR4 format details (textures, assets)
- Support additional regions beyond SouthernCA
- Performance optimization for large depth traces
- Batch processing multiple track databases

## Typical Workflows

### Visualizing a Track Network
1. Choose a track database from `track_databases/`
2. Identify starting section (use explore_trackdb.py to browse)
3. Create config.ini (or use `--generate-config` to create template):
   ```ini
   [database]
   track_database = track_databases/Mojave.r8
   start_section = 5306
   depth = 10
   ```
4. Run: `python visualize_switch_network.py`
5. Output: HTML file based on output_pattern (default: `{track_db}_{section}_depth{depth}.html`)

### Finding and Correcting Misaligned Tiles

#### Option 1: Scan from Track Database
1. Identify a track section in a known good area (use explore_trackdb.py)
2. Run: `python manage_tile_corrections.py scan --track-db <file> --start-section <num>`
3. Uses the tile from that section as the trusted home tile
4. Expands outward via BFS through aligned tiles

#### Option 2: Scan Specific Tile Range (Faster for Testing)
1. Identify a known good tile in the region
2. Run: `python manage_tile_corrections.py scan-range -10 -5 40 45 --home-tile -7,42`
3. Scans ALL tiles in the rectangular range X=[-10, -5], Z=[40, 45]
4. Uses specified home tile as trusted starting point
5. Multi-pass algorithm corrects chains of misaligned tiles

#### Common Options
- `--threshold 0.0005` - Gap threshold in degrees (default: 0.0005°)
- `--max-passes 20` - Maximum correction iterations (default: 20)
- `--output tile_corrections.csv` - Output file path

#### Notes
- Each scan starts fresh - does not load existing corrections
- All corrections calculated from scratch based on trusted home tile
- Output CSV can be used by visualize_switch_network.py to apply corrections
- Uncategorized tiles are those with no path to home tile through aligned tiles

## Output Files
- HTML visualizations: `track_databases/*_depth*.html`
- Tile corrections: `tile_corrections.csv` (project root)
- HTML assets: `html/` directory (currently empty)

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
