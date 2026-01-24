#!/usr/bin/env python3
"""
Multi-region configuration parser for Run8 Track Mapper.

Parses INI files with the following structure:

[visualization]
name = Southern California
tile_corrections = tile_corrections_socal.csv
industry_db = path/to/Regions/SouthernCA/Config.ind

[region.mojave]
display_name = Mojave Subdivision
route_prefix = 1
directory = path/to/V3Routes/BNSF_MojaveSub
enabled_by_default = true

[region.barstow]
...

[output]
output_dir = ./output/socal/
"""

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


# Standard filenames within each region directory
TRACK_DB_FILENAME = "TrackDatabase.r8"
SIGNAL_DB_FILENAME = "SignalHeadDatabase.r8"
AI_LOCATIONS_FILENAME = "AiSpecialLocations.r8"
INDUSTRY_DB_FILENAME = "Config.ind"
TERRAIN_DIR_NAME = "TerrainTiles"


@dataclass
class TileBasedConfig:
    """Configuration for tile-based plotting (non-geographic coordinates)"""
    home_tile: Tuple[int, int] = (0, 0)  # Reference tile for origin
    tile_width: float = 842.3            # Tile width in meters
    tile_height: float = 1023.2          # Tile height in meters


@dataclass
class ColorConfig:
    """Color configuration for visualization elements"""
    track: str = "#0066cc"              # Regular track sections
    track_selected: str = "#ff0000"     # Selected track sections
    track_hover: str = "#ffff00"        # Hover highlight color (yellow)
    switch: str = "#800080"             # Switch/turnout sections (purple)
    industry_track: str = "#00aa00"     # Industry track sections (green)
    signal_absolute: str = "#FF6B35"    # Absolute signals (orange)
    signal_intermediate: str = "#FFD700"  # Intermediate signals (gold)
    signal_border_single: str = "#000000"  # Single head signal border (black)
    signal_border_stacked: str = "#87CEEB"  # Multiple head signal border (light blue)
    background: str = "#333333"         # Background color (tile-based mode)


@dataclass
class RegionConfig:
    """Configuration for a single region"""
    id: str  # e.g., "mojave" (from section name [region.mojave])
    display_name: str
    route_prefix: int
    directory: Path
    enabled_by_default: bool = False
    terrain_tile_dir: Optional[Path] = None  # Path to terrain tiles (.tr4 files)
    track_color: Optional[str] = None  # Per-region track color (hex), falls back to global default

    @property
    def track_database(self) -> Path:
        return self.directory / TRACK_DB_FILENAME

    @property
    def signal_database(self) -> Path:
        return self.directory / SIGNAL_DB_FILENAME

    @property
    def ai_locations_database(self) -> Path:
        return self.directory / AI_LOCATIONS_FILENAME


@dataclass
class VisualizationConfig:
    """Complete configuration for a multi-region visualization"""
    name: str
    tile_corrections: Path
    region_dir: Path
    output_dir: Path
    regions: List[RegionConfig] = field(default_factory=list)
    colors: ColorConfig = field(default_factory=ColorConfig)
    tile_based: Optional[TileBasedConfig] = None
    initial_center: Optional[Tuple[float, float]] = None  # (lat, lon) for initial map center

    @property
    def industry_db(self) -> Path:
        """Path to industry database file (derived from region_dir)"""
        return self.region_dir / INDUSTRY_DB_FILENAME

    @property
    def terrain_tile_dir(self) -> Path:
        """Path to terrain tiles directory (derived from region_dir)"""
        return self.region_dir / TERRAIN_DIR_NAME


class ConfigError(Exception):
    """Raised when configuration is invalid"""
    pass


def parse_config(config_path: str) -> VisualizationConfig:
    """
    Parse a multi-region configuration file.

    Args:
        config_path: Path to the INI configuration file

    Returns:
        VisualizationConfig with all parsed settings

    Raises:
        ConfigError: If configuration is invalid or files are missing
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    parser = configparser.ConfigParser()
    parser.read(config_path)

    errors = []

    # Parse [visualization] section
    if 'visualization' not in parser:
        errors.append("[visualization] section is required")
    else:
        viz = parser['visualization']
        name = viz.get('name', '').strip()
        if not name:
            errors.append("[visualization] name is required")

        tile_corrections_str = viz.get('tile_corrections', '').strip()
        if not tile_corrections_str:
            errors.append("[visualization] tile_corrections is required")

        region_dir_str = viz.get('region_dir', '').strip()
        if not region_dir_str:
            errors.append("[visualization] region_dir is required")

        # Optional initial_center (format: "lat,lon" e.g., "34.9,-118.0")
        initial_center_str = viz.get('initial_center', '').strip()
        initial_center = None
        if initial_center_str:
            try:
                parts = initial_center_str.split(',')
                initial_center = (float(parts[0].strip()), float(parts[1].strip()))
            except (ValueError, IndexError):
                errors.append(f"[visualization] initial_center must be in format 'lat,lon' (e.g., '34.9,-118.0')")

    # Parse [output] section
    if 'output' not in parser:
        errors.append("[output] section is required")
    else:
        out = parser['output']
        output_dir_str = out.get('output_dir', '').strip()
        if not output_dir_str:
            errors.append("[output] output_dir is required")

    # Parse [region.*] sections
    region_sections = [s for s in parser.sections() if s.startswith('region.')]
    if not region_sections:
        errors.append("At least one [region.*] section is required")

    regions = []
    for section_name in region_sections:
        region_id = section_name[7:]  # Remove "region." prefix
        region = parser[section_name]

        display_name = region.get('display_name', '').strip()
        if not display_name:
            errors.append(f"[{section_name}] display_name is required")

        route_prefix_str = region.get('route_prefix', '').strip()
        if not route_prefix_str:
            errors.append(f"[{section_name}] route_prefix is required")
        else:
            try:
                route_prefix = int(route_prefix_str)
            except ValueError:
                errors.append(f"[{section_name}] route_prefix must be an integer")
                route_prefix = 0

        directory_str = region.get('directory', '').strip()
        if not directory_str:
            errors.append(f"[{section_name}] directory is required")

        enabled_str = region.get('enabled_by_default', 'false').strip().lower()
        enabled_by_default = enabled_str in ('true', 'yes', '1')

        # Optional terrain tile directory
        terrain_tile_dir_str = region.get('terrain_tile_dir', '').strip()
        terrain_tile_dir = Path(terrain_tile_dir_str) if terrain_tile_dir_str else None

        # Optional per-region track color
        track_color_str = region.get('track_color', '').strip()
        track_color = track_color_str if track_color_str else None

        if display_name and directory_str:
            regions.append(RegionConfig(
                id=region_id,
                display_name=display_name,
                route_prefix=route_prefix,
                directory=Path(directory_str),
                enabled_by_default=enabled_by_default,
                terrain_tile_dir=terrain_tile_dir,
                track_color=track_color
            ))

    # If we have basic parsing errors, raise now
    if errors:
        raise ConfigError("Configuration errors:\n  - " + "\n  - ".join(errors))

    # Parse [colors] section (optional - use defaults if not present)
    colors = ColorConfig()
    if 'colors' in parser:
        color_section = parser['colors']
        colors = ColorConfig(
            track=color_section.get('track', colors.track).strip(),
            track_selected=color_section.get('track_selected', colors.track_selected).strip(),
            track_hover=color_section.get('track_hover', colors.track_hover).strip(),
            switch=color_section.get('switch', colors.switch).strip(),
            industry_track=color_section.get('industry_track', colors.industry_track).strip(),
            signal_absolute=color_section.get('signal_absolute', colors.signal_absolute).strip(),
            signal_intermediate=color_section.get('signal_intermediate', colors.signal_intermediate).strip(),
            signal_border_single=color_section.get('signal_border_single', colors.signal_border_single).strip(),
            signal_border_stacked=color_section.get('signal_border_stacked', colors.signal_border_stacked).strip(),
            background=color_section.get('background', colors.background).strip(),
        )

    # Parse [tile_based_plot] section (optional)
    tile_based = None
    if 'tile_based_plot' in parser:
        tb_section = parser['tile_based_plot']

        # Parse home_tile (format: "x,z" e.g., "209,-10")
        home_tile_str = tb_section.get('home_tile', '0,0').strip()
        try:
            parts = home_tile_str.split(',')
            home_tile = (int(parts[0].strip()), int(parts[1].strip()))
        except (ValueError, IndexError):
            errors.append(f"[tile_based_plot] home_tile must be in format 'x,z' (e.g., '209,-10')")
            home_tile = (0, 0)

        tile_width = float(tb_section.get('tile_width', '842.3').strip())
        tile_height = float(tb_section.get('tile_height', '1023.2').strip())

        tile_based = TileBasedConfig(
            home_tile=home_tile,
            tile_width=tile_width,
            tile_height=tile_height
        )

    # Build the config object
    config = VisualizationConfig(
        name=name,
        tile_corrections=Path(tile_corrections_str),
        region_dir=Path(region_dir_str),
        output_dir=Path(output_dir_str),
        regions=regions,
        colors=colors,
        tile_based=tile_based,
        initial_center=initial_center
    )

    # Validate that all files exist
    file_errors = []

    if not config.tile_corrections.exists():
        file_errors.append(f"[visualization] tile_corrections file not found: {config.tile_corrections}")

    if not config.region_dir.exists():
        file_errors.append(f"[visualization] region_dir not found: {config.region_dir}")
    else:
        # Validate derived paths
        if not config.industry_db.exists():
            file_errors.append(f"[visualization] industry_db not found: {config.industry_db} (derived from region_dir)")
        if not config.terrain_tile_dir.exists():
            file_errors.append(f"[visualization] terrain tile directory not found: {config.terrain_tile_dir} (derived from region_dir)")

    for region in config.regions:
        if not region.directory.exists():
            file_errors.append(f"[region.{region.id}] directory not found: {region.directory}")
        else:
            # Check for required files in directory
            if not region.track_database.exists():
                file_errors.append(f"[region.{region.id}] track database not found: {region.track_database}")
            if not region.signal_database.exists():
                # Signal database is optional (dark territory regions have no signals)
                print(f"  Note: [region.{region.id}] has no signal database (dark territory) - signals will not be rendered")
            if not region.ai_locations_database.exists():
                # AI locations database is optional
                print(f"  Note: [region.{region.id}] has no AI locations database - AI locations will not be rendered")

        # Validate terrain_tile_dir if specified
        if region.terrain_tile_dir and not region.terrain_tile_dir.exists():
            file_errors.append(f"[region.{region.id}] terrain_tile_dir not found: {region.terrain_tile_dir}")

    if file_errors:
        raise ConfigError("Missing files:\n  - " + "\n  - ".join(file_errors))

    return config


def generate_sample_config() -> str:
    """Generate a sample configuration file template"""
    return '''# Multi-Region Track Mapper Configuration
#
# This file defines a visualization containing multiple regions.

[visualization]
# Display name for the visualization
name = Southern California

# Path to tile corrections CSV (global for all regions)
tile_corrections = tile_corrections_socal.csv

# Path to the region directory containing Config.ind and TerrainTiles
# The industry database (Config.ind) and terrain tiles directory (TerrainTiles)
# are automatically derived from this path
region_dir = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\Regions\\SouthernCA

# (Optional) Initial map center as lat,lon (e.g., 34.9,-118.0)
# If not specified, uses a default center
# initial_center = 34.9,-118.0

[region.mojave]
# Human-readable name for this region
display_name = Mojave Subdivision

# Route prefix integer for filtering industries
route_prefix = 1

# Directory containing TrackDatabase.r8, SignalHeadDatabase.r8, AiSpecialLocations.r8
directory = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\BNSF_MojaveSub

# (Optional) Path to terrain tile directory containing .tr4 files
# If not specified, uses the default SouthernCA tile location
terrain_tile_dir = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\Regions\\SouthernCA\\TerrainTiles

# Whether this region is enabled by default when the map loads
enabled_by_default = true

# (Optional) Per-region track color - overrides the global track color
# track_color = #0066cc

[region.barstow]
display_name = Barstow Subdivision
route_prefix = 2
directory = C:\\Run8Studios\\Run8 Train Simulator V3\\Content\\V3Routes\\BNSF_BarstowSub
# terrain_tile_dir can be omitted to use the default location
enabled_by_default = false

# (Optional) Per-region track color - overrides the global track color
# track_color = #2266ff

# Add more [region.*] sections as needed...

[colors]
# All colors are optional - defaults are shown below
# Colors can be hex (#RRGGBB) or named colors

# Track colors
track = #0066cc
track_selected = #ff0000
track_hover = #ffff00
switch = #800080
industry_track = #00aa00

# Signal colors
signal_absolute = #FF6B35
signal_intermediate = #FFD700
signal_border_single = #000000
signal_border_stacked = #87CEEB

[output]
# Directory where output files will be written
output_dir = ./output/socal/
'''


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: config_parser.py <config.ini>")
        print("       config_parser.py --generate")
        sys.exit(1)

    if sys.argv[1] == '--generate':
        print(generate_sample_config())
    else:
        try:
            config = parse_config(sys.argv[1])
            print(f"Configuration loaded successfully!")
            print(f"  Name: {config.name}")
            print(f"  Tile corrections: {config.tile_corrections}")
            print(f"  Region dir: {config.region_dir}")
            print(f"  Industry DB (derived): {config.industry_db}")
            print(f"  Terrain tiles (derived): {config.terrain_tile_dir}")
            print(f"  Output directory: {config.output_dir}")
            print(f"  Regions: {len(config.regions)}")
            for region in config.regions:
                print(f"    - {region.display_name} (prefix={region.route_prefix}, enabled={region.enabled_by_default})")
                print(f"      Directory: {region.directory}")
                if region.terrain_tile_dir:
                    print(f"      Terrain tiles: {region.terrain_tile_dir}")
        except ConfigError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
